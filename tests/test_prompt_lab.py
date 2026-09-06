# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline tests: real tiny Transformers model, no downloaded weights or API calls."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

from heretic.prompt_lab.cli import main
from heretic.prompt_lab.data import Example, Template, read_examples, write_json
from heretic.prompt_lab.engine import (
    PromptEngine,
    TrainConfig,
    assert_unseen,
    evaluate,
    load_artifact,
    provenance,
    save_artifact,
    search_templates,
    train_soft_prompt,
)


def tiny_model():
    torch.manual_seed(7)
    words = [
        "<unk>",
        "<bos>",
        "<eos>",
        "<pad>",
        "user",
        "assistant",
        "system",
        "red",
        "green",
        "blue",
        "yellow",
        "Please",
        "answer",
        "the",
        "question",
        "clearly",
        ".",
        "only",
        "Task",
        ":",
        "a",
        "b",
        "c",
        "d",
    ]
    backend = Tokenizer(
        WordLevel({word: i for i, word in enumerate(words)}, unk_token="<unk>")
    )
    backend.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="<unk>",
        bos_token="<bos>",
        eos_token="<eos>",
        pad_token="<pad>",
    )
    tokenizer.chat_template = (
        "{{ bos_token }}{% for message in messages %}"
        "{{ message['role'] + ' ' + message['content'] + ' ' + eos_token }}"
        "{% endfor %}{% if add_generation_prompt %}assistant {% endif %}"
    )
    model = GPT2LMHeadModel(
        GPT2Config(
            vocab_size=len(tokenizer),
            n_positions=128,
            n_embd=24,
            n_layer=1,
            n_head=2,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.pad_token_id,
        )
    )
    return model, tokenizer


class PromptLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.model, self.tokenizer = tiny_model()
        self.engine = PromptEngine(
            self.model, self.tokenizer, model_id="test-tiny", max_length=128
        )
        self.train = [Example("tr", "red", "blue", "train")]
        self.validation = [Example("va", "green", "blue", "validation")]
        self.test = [Example("te", "yellow", "blue", "test")]

    def test_only_soft_prompt_changes_and_validation_loss_improves(self):
        before = {
            name: value.clone() for name, value in self.model.state_dict().items()
        }
        config = TrainConfig(tokens=4, epochs=8, batch_size=2, learning_rate=0.01)
        soft, history = train_soft_prompt(
            self.engine, self.train, self.validation, Template(), config
        )
        self.assertLess(
            min(row["validation_target_nll"] for row in history[1:]),
            history[0]["validation_target_nll"],
        )
        self.assertAlmostEqual(
            self.engine.mean_loss(self.validation, Template(), soft),
            min(row["validation_target_nll"] for row in history),
            places=6,
        )
        self.assertEqual(soft.shape, (4, 24))
        for name, value in self.model.state_dict().items():
            self.assertTrue(torch.equal(value, before[name]), name)
        self.assertTrue(
            all(not p.requires_grad and p.grad is None for p in self.model.parameters())
        )

    def test_target_loss_matches_masked_causal_lm_loss(self):
        example = Example("x", "red green", "blue yellow", "test")
        prompt = self.engine.encode_prompt(example.prompt, Template())
        target = self.engine.target_ids(example.target)
        ids = torch.tensor([prompt + target])
        labels = ids.clone()
        labels[:, : len(prompt)] = -100
        with torch.no_grad():
            expected = self.model(input_ids=ids, labels=labels).loss
            actual = self.engine.loss(example, Template())
        self.assertAlmostEqual(float(actual), float(expected), places=6)

    def test_first_target_token_receives_prefix_gradient(self):
        soft = torch.nn.Parameter(torch.randn(3, 24) * 0.02)
        self.engine.loss(self.train[0], Template(), soft).backward()
        assert soft.grad is not None
        self.assertGreater(float(soft.grad.abs().sum()), 0.0)

    def test_generation_does_not_trim_continuation_as_if_it_were_input_ids(self):
        ids = torch.tensor([self.engine.encode_prompt("red", Template())])
        with torch.no_grad():
            expected_ids = self.model.generate(
                input_ids=ids,
                attention_mask=torch.ones_like(ids),
                do_sample=False,
                num_beams=1,
                max_new_tokens=4,
            )[0, ids.shape[1] :]
        actual = self.engine.generate("red", Template(), max_new_tokens=4)
        self.assertEqual(
            actual, self.tokenizer.decode(expected_ids, skip_special_tokens=True)
        )

    def test_sequence_overflow_includes_virtual_tokens_and_target(self):
        limited = PromptEngine(
            self.model, self.tokenizer, model_id="test-tiny", max_length=8
        )
        with self.assertRaisesRegex(ValueError, "never silently truncated"):
            limited.loss(self.train[0], Template(), torch.zeros(8, 24))
        with self.assertRaisesRegex(ValueError, "exceeding limit"):
            limited.generate("red", Template(), max_new_tokens=20)

    def test_chat_system_role_is_preserved(self):
        engine = PromptEngine(
            self.model, self.tokenizer, model_id="test-tiny", system="yellow"
        )
        rendered = self.tokenizer.decode(engine.encode_prompt("red", Template()))
        self.assertIn("system yellow", rendered)
        self.assertIn("user red", rendered)
        self.assertTrue(rendered.endswith("assistant"))

    def test_search_never_accepts_test_data(self):
        with self.assertRaisesRegex(ValueError, "validation split"):
            search_templates(self.engine, self.test, [Template()])
        template, scores = search_templates(
            self.engine, self.validation, [Template(), Template("short", "", " only")]
        )
        self.assertEqual(template.name, scores[0]["template"]["name"])
        self.assertLessEqual(
            scores[0]["validation_target_nll"], scores[1]["validation_target_nll"]
        )

    def test_no_refusal_heuristic_is_misreported_as_success(self):
        report = evaluate(self.engine, self.test, Template(), torch.zeros(2, 24), 3)
        self.assertEqual(set(report["summary"]), {"baseline", "soft"})
        for row in report["examples"]:
            self.assertIsNone(row["behavior_success"])
            self.assertIsNone(row["input_guardrail_pass"])
            self.assertIsNone(row["output_guardrail_pass"])

    def test_prompt_overlap_is_detected_even_with_new_ids(self):
        artifact = {"selection_data": provenance(self.train + self.validation)}
        with self.assertRaisesRegex(ValueError, "overlaps"):
            assert_unseen([Example("fresh", " RED  ", "blue", "test")], artifact)
        assert_unseen(self.test, artifact)
        with self.assertRaisesRegex(ValueError, "overlap"):
            train_soft_prompt(
                self.engine,
                self.train,
                [Example("new", "RED", "blue", "validation")],
                Template(),
                TrainConfig(epochs=1),
            )

    def test_artifact_round_trip_and_model_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact"
            soft = torch.randn(3, 24)
            metadata = {
                "kind": "soft",
                "engine": self.engine.identity(),
                "template": asdict(Template()),
                "training": {"tokens": 3},
                "selection_data": provenance(self.train),
            }
            save_artifact(path, metadata, soft)
            _, loaded = load_artifact(path, self.engine)
            assert loaded is not None
            self.assertTrue(torch.equal(soft, loaded))
            self.assertEqual(
                self.engine.generate("red", Template(), soft, 3),
                self.engine.generate("red", Template(), loaded, 3),
            )
            other = PromptEngine(
                self.model, self.tokenizer, model_id="different", max_length=128
            )
            with self.assertRaisesRegex(ValueError, "differ"):
                load_artifact(path, other)
            with self.assertRaises(FileExistsError):
                save_artifact(path, metadata, soft)
            with (path / "soft_prompt.pt").open("ab") as file:
                file.write(b"modified")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_artifact(path)

    def test_dataset_validation_and_literal_template_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.jsonl"
            rows = [asdict(e) for e in self.train + self.validation + self.test]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )
            self.assertEqual(len(read_examples(path)), 3)
            rows[-1]["prompt"] = "  RED "
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_examples(path)
        self.assertEqual(Template("t", "a", "b").render('{"key": 1}'), 'a{"key": 1}b')

    def test_cli_offline_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_model = root / "model"
            self.model.save_pretrained(local_model)
            self.tokenizer.save_pretrained(local_model)
            data = root / "data.jsonl"
            data.write_text(
                "\n".join(
                    json.dumps(asdict(e))
                    for e in self.train + self.validation + self.test
                ),
                encoding="utf-8",
            )
            templates = root / "templates.json"
            write_json(
                templates, [asdict(Template()), asdict(Template("short", "", " only"))]
            )
            common = [
                "--model",
                str(local_model),
                "--data",
                str(data),
                "--local-files-only",
                "--device",
                "cpu",
            ]
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                main(
                    [
                        "search",
                        *common,
                        "--templates",
                        str(templates),
                        "--output",
                        str(root / "text"),
                    ]
                )
                main(
                    [
                        "train",
                        *common,
                        "--epochs",
                        "2",
                        "--tokens",
                        "3",
                        "--template-artifact",
                        str(root / "text"),
                        "--output",
                        str(root / "soft"),
                    ]
                )
                main(
                    [
                        "evaluate",
                        "--artifact",
                        str(root / "soft"),
                        "--data",
                        str(data),
                        "--output",
                        str(root / "eval"),
                        "--local-files-only",
                        "--device",
                        "cpu",
                        "--max-new-tokens",
                        "3",
                    ]
                )
                main(
                    [
                        "generate",
                        "--artifact",
                        str(root / "soft"),
                        "--prompt",
                        "yellow",
                        "--local-files-only",
                        "--device",
                        "cpu",
                        "--max-new-tokens",
                        "3",
                    ]
                )
                main(["render", "--artifact", str(root / "text"), "--prompt", "yellow"])
            report = json.loads((root / "eval" / "report.json").read_text())
            self.assertEqual(report["summary"]["soft"]["count"], 1)
            self.assertTrue(all(row["id"] == "te" for row in report["examples"]))


if __name__ == "__main__":
    unittest.main()
