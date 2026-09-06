# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from fastapi.testclient import TestClient
from test_prompt_lab import tiny_model

from heretic.chat_service import app as api
from heretic.chat_service.optimizers import concept_features, mutate
from heretic.chat_service.report import judge, outcome, write_html
from heretic.chat_service.runtime import JAPANESE_SYSTEM, ChatEngine, Runtime
from heretic.chat_service.tools import calculate, execute_tool
from heretic.prompt_lab.data import Template


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
        runtime = Runtime.__new__(Runtime)
        runtime.methods = {"baseline": (Template(), None)}
        with (
            patch.object(api.app.state, "runtime", runtime, create=True),
            patch.object(api.app.state, "busy", asyncio.Lock(), create=True),
        ):
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

    def test_complete_response_identifies_the_loaded_audit_context(self):
        runtime = Runtime.__new__(Runtime)
        runtime.methods = {"baseline": (Template(), None)}
        with (
            patch.object(api.app.state, "runtime", runtime, create=True),
            patch.object(api.app.state, "busy", asyncio.Lock(), create=True),
            patch.object(
                api.app.state, "audit_context_id", "loaded-context", create=True
            ),
            patch.object(
                api.app.state, "audit_context", {"engine": "test"}, create=True
            ),
            patch.object(
                runtime,
                "chat",
                return_value={
                    "response": "回答",
                    "method": "baseline",
                    "tools": [],
                    "seconds": 0,
                },
            ),
        ):
            client = TestClient(api.app)
            context = client.get("/api/audit-context").json()
            response = client.post(
                "/api/chat/complete",
                json={"messages": [{"role": "user", "content": "質問"}]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["context_id"], context["id"])
            self.assertEqual(response.json()["response"], "回答")

    def test_openui_sse_protocol_contains_response_and_completion_marker(self):
        runtime = Runtime.__new__(Runtime)
        runtime.model_id = "test"
        runtime.methods = {"baseline": (Template(), None)}
        with (
            patch.object(
                runtime,
                "chat",
                return_value={"response": "hello", "tools": [], "seconds": 0},
            ),
            patch.object(api.app.state, "runtime", runtime, create=True),
            patch.object(api.app.state, "busy", asyncio.Lock(), create=True),
        ):
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


if __name__ == "__main__":
    unittest.main()
