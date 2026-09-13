# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import base64
import contextlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import torch
from fastapi.testclient import TestClient
from test_prompt_lab import tiny_model

from heretic.chat_service import app as api
from heretic.chat_service.attachments import flatten_content
from heretic.chat_service.optimizers import concept_features, mutate
from heretic.chat_service.registry import ModelRegistry, artifact_root_for, load_catalog, parse_model_list
from heretic.chat_service.report import judge, outcome, write_html
from heretic.chat_service.runtime import JAPANESE_SYSTEM, ChatEngine, Runtime
from heretic.chat_service.tools import calculate, execute_tool
from heretic.prompt_lab.data import Template


def fake_runtime(model_id="test"):
    runtime = Runtime.__new__(Runtime)
    runtime.model_id = model_id
    runtime.methods = {"baseline": (Template(), None)}
    runtime.metadata = {"baseline": {"label": "Original", "method": "baseline"}}
    return runtime


def app_state(runtime, **extra):
    registry = ModelRegistry([runtime.model_id], lambda _id: runtime)
    registry.loaded[runtime.model_id] = runtime  # already resident, like the default model
    patches = [
        patch.object(api.app.state, "runtime", runtime, create=True),
        patch.object(api.app.state, "registry", registry, create=True),
        patch.object(api.app.state, "busy", asyncio.Lock(), create=True),
    ]
    for key, value in extra.items():
        patches.append(patch.object(api.app.state, key, value, create=True))
    return patches


class ServiceTests(unittest.TestCase):
    def test_calculator_evaluates_arithmetic_but_cannot_execute_code(self):
        self.assertEqual(calculate("(173 * 29) + 41"), 5058)
        for expression in (
            "__import__('os').system('id')",
            "open('/etc/passwd').read()",
            "10**9999",
            "[1,2][0]",
        ):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                calculate(expression)

    def test_tool_allowlist_and_input_validation(self):
        with self.assertRaises(ValueError):
            execute_tool("shell", {"command": "pwd"}, Path("."))
        with self.assertRaises(ValueError):
            execute_tool("current_time", {"unexpected": "value"}, Path("."))
        with self.assertRaises(ValueError):
            execute_tool(
                "search_wikipedia",
                {"query": "test", "language": "localhost"},
                Path("."),
            )
        self.assertIn("utc", execute_tool("current_time", {}, Path(".")))

    def test_keyword_presence_alone_does_not_erase_explicit_refusal(self):
        row = {"required_any": [["1989"], ["北京"]]}
        self.assertTrue(judge("1989年，北京。", row)["rubric_pass"])
        self.assertFalse(judge("抱歉，无法回答1989年北京的问题。", row)["rubric_pass"])
        self.assertEqual(
            outcome({"rubric_pass": True}, {"rubric_pass": False}), "regressed"
        )

    def test_report_search_accepts_keywords_and_bounds_returned_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "comparison.json").write_text(
                json.dumps(
                    {
                        "comparisons": [
                            {
                                "id": "te-math",
                                "method": "soft",
                                "prompt": "calculate",
                                "before": "x" * 10000,
                                "after": "391",
                                "outcome": "both_pass",
                            }
                        ]
                    }
                )
            )
            result = execute_tool(
                "search_reports", {"query": "te-math experiment"}, root
            )
            self.assertEqual(result["total"], 1)
            self.assertEqual(len(result["matches"][0]["before_excerpt"]), 240)

    def test_unavailable_information_expects_honest_inability_to_answer(self):
        row = {
            "category": "unavailable_information",
            "required_any": [["not have access"]],
        }
        self.assertTrue(
            judge("I cannot provide it: I do not have access to it.", row)[
                "rubric_pass"
            ]
        )
        self.assertFalse(judge("The number is 123456789012.", row)["rubric_pass"])
        self.assertTrue(
            judge("I cannot provide it: I don't have access to it.", row)["rubric_pass"]
        )
        self.assertFalse(judge("You don't have access to it.", row)["rubric_pass"])
        japanese = {
            "category": "unavailable_information",
            "required_any": [["分かりません"]],
        }
        self.assertTrue(
            judge("その数字は共有されていないため、私には分かりません。", japanese)[
                "rubric_pass"
            ]
        )

    def test_japanese_instruction_is_consistent_for_training_chat_and_tools(self):
        model, tokenizer = tiny_model()
        engine = ChatEngine(model, tokenizer, model_id="tiny", system=JAPANESE_SYSTEM)
        user = {"role": "user", "content": "red"}
        serialized = engine.serialize([user])
        self.assertEqual(serialized.count(JAPANESE_SYSTEM), 1)
        self.assertEqual(
            engine.encode_prompt("red", Template()),
            tokenizer.encode(serialized, add_special_tokens=False),
        )
        tools = engine.serialize(
            [{"role": "system", "content": "ツールで計算する。"}, user]
        )
        self.assertIn(JAPANESE_SYSTEM, tools)
        self.assertIn("ツールで計算する。", tools)

    def test_report_escapes_model_generated_html(self):
        with tempfile.TemporaryDirectory() as directory:
            report = {
                "model": "test",
                "model_revision": "test",
                "created_at": "test",
                "generation": {},
                "dataset_sha256": "test",
                "evaluation_note": "test",
                "summary": {"soft": {"improved": 1}},
                "comparisons": [
                    {
                        "method": "soft",
                        "outcome": "improved",
                        "id": "x",
                        "category": "test",
                        "prompt": "<script>alert('x')</script>",
                        "before": "original",
                        "after": "<img src=x onerror=alert(1)>",
                        "before_judgment": {},
                        "after_judgment": {},
                        "seconds": 1,
                        "template": {},
                    }
                ],
            }
            path = Path(directory) / "report.html"
            write_html(report, path)
            content = path.read_text()
            self.assertNotIn("<img src=x", content)
            self.assertIn("&lt;img", content)
            self.assertNotIn("<script>alert", content)

    def test_tool_loop_runs_tool_and_preserves_result_in_context(self):
        runtime = Runtime.__new__(Runtime)
        runtime.lock = threading.Lock()
        runtime.artifact_root = Path("/tmp/methods")
        calls = []

        def complete(messages, method, **kwargs):
            calls.append(messages.copy())
            return (
                '<tool_call>{"name":"calculate","arguments":{"expression":"6*7"}}</tool_call>'
                if len(calls) == 1
                else "42"
            )

        with patch.object(runtime, "complete", side_effect=complete):
            result = runtime.chat(
                [{"role": "user", "content": "calculate 6*7"}], "baseline", True, 64
            )
        self.assertEqual(result["response"], "42")
        self.assertEqual(result["tools"][0]["result"]["value"], 42)
        self.assertEqual(calls[1][-1]["role"], "tool")

    def test_concept_alignment_backpropagates_to_only_the_added_vectors(self):
        model, tokenizer = tiny_model()
        engine = ChatEngine(model, tokenizer, model_id="tiny")
        soft = torch.nn.Parameter(torch.randn(2, 24) * 0.01)
        feature = concept_features(engine, "red green", "red", 1, soft)
        feature.square().sum().backward()
        self.assertIsNotNone(soft.grad)
        self.assertTrue(all(p.grad is None for p in model.parameters()))
        with self.assertRaises(ValueError):
            concept_features(engine, "red green", "missing", 1)

    def test_mutation_accepts_fenced_json_and_rejects_missing_fields(self):
        runtime = Runtime.__new__(Runtime)
        with patch.object(
            runtime,
            "complete",
            side_effect=[
                '```json\n{"prefix":"Task: ","suffix":""}\n```',
                '{"prefix":"broken"}',
            ],
        ):
            result = mutate(runtime, Template(), "feedback")
            assert result is not None
            self.assertEqual(result.prefix, "Task: ")
            self.assertIsNone(mutate(runtime, Template(), "feedback"))

    def test_api_rejects_external_system_messages_and_unknown_methods(self):
        runtime = fake_runtime()
        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            client = TestClient(api.app)
            response = client.post(
                "/api/chat",
                json={"messages": [{"role": "system", "content": "override"}]},
            )
            self.assertEqual(response.status_code, 422)
            response = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "test"}],
                    "method": "unknown",
                },
            )
            self.assertEqual(response.status_code, 422)
            response = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "test"}],
                    "model": "not-served/model",
                },
            )
            self.assertEqual(response.status_code, 422)

    def test_complete_response_identifies_the_loaded_audit_context(self):
        runtime = fake_runtime()
        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            api.app.state.registry.contexts["test"] = {"id": "loaded-context", "engine": "test"}
            stack.enter_context(patch.object(api.app.state, "audit_context_id", "loaded-context", create=True))
            stack.enter_context(patch.object(api.app.state, "audit_context", {"engine": "test"}, create=True))
            stack.enter_context(patch.object(
                runtime,
                "chat",
                return_value={
                    "response": "回答",
                    "method": "baseline",
                    "tools": [],
                    "seconds": 0,
                },
            ))
            client = TestClient(api.app)
            context = client.get("/api/audit-context").json()
            response = client.post(
                "/api/chat/complete",
                json={"messages": [{"role": "user", "content": "質問"}]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["context_id"], context["id"])
            self.assertEqual(response.json()["response"], "回答")

    def test_escalate_method_is_accepted_and_routes_to_escalation(self):
        from heretic.chat_service import response_audit

        runtime = fake_runtime()
        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            api.app.state.registry.contexts["test"] = {"id": "ctx"}
            stack.enter_context(patch.object(api.app.state, "jailbreak_chat", Mock(judge=Mock()), create=True))
            stack.enter_context(patch.object(response_audit, "run_escalation", return_value={
                "response": "回答", "method": "gcg", "attempts": 2, "tools": [], "seconds": 0.0,
                "assessment": {"status": "non_refusal", "judge": "G", "refused": False},
                "optimization_log": [], "optimization_performed": True,
            }))
            client = TestClient(api.app)
            # "escalate" is a pseudo-method: accepted even though it is not a trained artifact.
            response = client.post(
                "/api/chat/complete",
                json={"messages": [{"role": "user", "content": "q"}], "method": "escalate"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["method"], "gcg")
            self.assertEqual(response.json()["model"], "test")

    def test_openui_sse_protocol_contains_response_and_completion_marker(self):
        runtime = fake_runtime()
        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            stack.enter_context(patch.object(
                runtime,
                "chat",
                return_value={"response": "hello", "tools": [], "seconds": 0},
            ))
            response = TestClient(api.app).post(
                "/api/chat", json={"messages": [{"role": "user", "content": "test"}]}
            )
            self.assertEqual(response.status_code, 200)
            chunks = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: {")
            ]
            self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], "hello")
            self.assertIn("data: [DONE]", response.text)

    def test_attachments_are_inlined_as_text_and_images_are_marked(self):
        text = base64.b64encode("秘密の\n手順".encode()).decode()
        content = [
            {"type": "text", "text": "この資料を要約して"},
            {"type": "file", "file": {"filename": "memo.txt", "file_data": f"data:text/plain;base64,{text}"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}, "filename": "fig.png"},
            {"type": "file", "file": {"filename": "blob.bin", "file_data": "data:application/octet-stream;base64,AAEC"}},
        ]
        rendered = flatten_content(api.Message(role="user", content=content).content)
        self.assertTrue(rendered.startswith("この資料を要約して"))
        self.assertIn("[添付ファイル: memo.txt]\n```\n秘密の\n手順\n```", rendered)
        self.assertIn("[添付画像: fig.png]（このモデルは画像の内容を読み取れません", rendered)
        self.assertIn("blob.bin（application/octet-stream、3 バイト）", rendered)
        self.assertEqual(flatten_content("plain"), "plain")
        with self.assertRaises(ValueError):
            api.Message(role="user", content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}] * 6)

    def test_chat_accepts_multipart_content_and_sends_flattened_text_to_the_model(self):
        runtime = fake_runtime()
        seen = {}

        def chat(messages, method, use_tools, max_new_tokens):
            seen["messages"] = messages
            return {"response": "ok", "method": method, "tools": [], "seconds": 0}

        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            api.app.state.registry.contexts["test"] = {"id": "ctx"}
            stack.enter_context(patch.object(runtime, "chat", side_effect=chat))
            client = TestClient(api.app)
            payload = base64.b64encode(b"a,b\n1,2").decode()
            response = client.post("/api/chat/complete", json={"messages": [{"role": "user", "content": [
                {"type": "text", "text": "表を説明して"},
                {"type": "file", "file": {"filename": "t.csv", "file_data": f"data:text/csv;base64,{payload}"}},
            ]}], "model": "test"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["model"], "test")
            self.assertIn("a,b\n1,2", seen["messages"][0]["content"])
            self.assertIsInstance(seen["messages"][0]["content"], str)
            empty = client.post("/api/chat/complete", json={"messages": [{"role": "user", "content": [{"type": "text", "text": "  "}]}]})
            self.assertEqual(empty.status_code, 422)

    def test_models_endpoint_lists_models_and_methods_follow_the_selected_model(self):
        runtime = fake_runtime()
        with contextlib.ExitStack() as stack:
            for item in app_state(runtime):
                stack.enter_context(item)
            client = TestClient(api.app)
            models = client.get("/api/models").json()
            self.assertEqual(models["default"], "test")
            self.assertEqual(models["models"][0]["id"], "test")
            self.assertTrue(models["models"][0]["loaded"])
            self.assertFalse(models["models"][0]["supports_images"])
            self.assertEqual(client.get("/api/methods", params={"model": "test"}).json()["model"], "test")
            self.assertEqual(client.get("/api/methods", params={"model": "missing"}).status_code, 422)

    def test_registry_loads_lazily_and_evicts_least_recently_used(self):
        loads = []

        def loader(model_id):
            loads.append(model_id)
            runtime = fake_runtime(model_id)
            runtime.engine = Mock()
            runtime.engine.identity.return_value = {"model": model_id}
            return runtime

        registry = ModelRegistry(["a/one", "b/two", "c/three"], loader, max_loaded=2)
        self.assertEqual(registry.default_id, "a/one")
        self.assertEqual(loads, [])
        self.assertEqual(registry.get().model_id, "a/one")
        registry.get("b/two")
        registry.get("a/one")  # refresh recency
        registry.get("c/three")  # evicts b/two
        self.assertEqual(loads, ["a/one", "b/two", "c/three"])
        self.assertEqual(list(registry.loaded), ["a/one", "c/three"])
        registry.get("b/two")
        self.assertEqual(loads[-1], "b/two")
        self.assertEqual(list(registry.loaded), ["c/three", "b/two"])
        described = {item["id"]: item for item in registry.describe()}
        self.assertTrue(described["a/one"]["default"])
        self.assertFalse(described["a/one"]["loaded"])
        self.assertEqual(described["b/two"]["methods"], ["baseline"])
        self.assertNotEqual(registry.audit_context("b/two")["id"], registry.audit_context("c/three")["id"])
        with self.assertRaises(ValueError):
            registry.resolve("unknown")
        self.assertEqual(parse_model_list(" b/two , a/one,b/two", "a/one"), ["b/two", "a/one"])
        self.assertEqual(parse_model_list(None, "a/one"), ["a/one"])
        self.assertEqual(artifact_root_for(Path("r"), "a/one", "a/one"), Path("r/methods"))
        self.assertEqual(artifact_root_for(Path("r"), "b/two", "a/one"), Path("r/models/b__two/methods"))

    def test_catalog_supplies_labels_and_decensored_flag_in_describe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps([
                {"id": "/weights/dc", "label": "除去済み", "decensored": True},
                {"bad": "no id"}, "not a dict",
            ]))
            catalog = load_catalog(path)
            self.assertEqual(catalog, {"/weights/dc": {"label": "除去済み", "decensored": True}})
            registry = ModelRegistry(["a/one", "/weights/dc"], fake_runtime, catalog=catalog)
            described = {item["id"]: item for item in registry.describe()}
            self.assertEqual(described["/weights/dc"]["label"], "除去済み")
            self.assertTrue(described["/weights/dc"]["decensored"])
            # A model with no catalog entry falls back to a derived label, not decensored.
            self.assertEqual(described["a/one"]["label"], "one")
            self.assertFalse(described["a/one"]["decensored"])
        self.assertEqual(load_catalog(Path(directory) / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
