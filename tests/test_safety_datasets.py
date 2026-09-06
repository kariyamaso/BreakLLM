# SPDX-License-Identifier: AGPL-3.0-or-later
import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from heretic.chat_service.safety_datasets import (
    PURPOSE,
    SOURCES,
    digest,
    parse_source,
    prepare_dataset,
    validate_dataset,
    verify_sources,
)


def csv_bytes(records):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode()


def harmbench_row(identity="one", category="standard", context=""):
    return {
        "Behavior": "Summarize the supplied text.",
        "FunctionalCategory": category,
        "SemanticCategory": "general",
        "Tags": "",
        "ContextString": context,
        "BehaviorID": identity,
    }


def dataset_fixture():
    rows, stats = parse_source(
        "advbench", csv_bytes([{"goal": "Explain a rainbow.", "target": "unused"}])
    )
    spec = SOURCES["advbench"]
    return {
        "schema_version": 1,
        "purpose": PURPOSE,
        "rows": rows,
        "sources": [
            {
                "name": "advbench",
                **spec,
                **stats,
                "url": f"{spec['base_url']}/{spec['revision']}/{spec['filename']}",
            }
        ],
    }


class SafetyDatasetsTests(unittest.TestCase):
    def test_advbench_drops_target_and_retains_duplicate_questions(self):
        original = {"goal": "Explain a rainbow.", "target": "Do not copy this target."}
        rows, stats = parse_source("advbench", csv_bytes([original, original]))
        self.assertEqual(stats["included_count"], 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual(rows[0]["prompt_sha256"], rows[1]["prompt_sha256"])
        self.assertNotIn("target", rows[0])

    def test_context_is_preserved_and_unsupported_categories_are_accounted(self):
        records = [
            harmbench_row(),
            harmbench_row("context", "contextual", "Line one.\nLine two.\n"),
            harmbench_row("copyright", "copyright"),
            harmbench_row("image", "multimodal"),
            harmbench_row("future", "future_category"),
        ]
        rows, stats = parse_source("harmbench", csv_bytes(records))
        self.assertEqual(
            rows[1]["prompt"], "Line one.\nLine two.\n\n\nSummarize the supplied text."
        )
        self.assertEqual(rows[1]["context_sha256"], digest("Line one.\nLine two.\n"))
        self.assertEqual(stats["source_count"], 5)
        self.assertEqual(stats["included_count"], 2)
        self.assertEqual(
            stats["excluded_counts"],
            {"copyright": 1, "multimodal": 1, "future_category": 1},
        )
        self.assertEqual(set(stats["exclusion_reasons"]), set(stats["excluded_counts"]))

    def test_japanese_text_and_metadata_are_preserved(self):
        record = {
            "id": "17",
            "question": "虹を説明してください。",
            "risk_area": "評価用",
            "types_of_harm": "一般",
            "specific_harms": "合成テスト",
        }
        rows, _ = parse_source("do_not_answer_ja", json.dumps([record]).encode())
        self.assertEqual(rows[0]["id"], "do_not_answer_ja:17")
        self.assertEqual(rows[0]["prompt"], record["question"])
        self.assertEqual(rows[0]["language"], "ja")
        self.assertEqual(rows[0]["risk_area"], "評価用")

    def test_malformed_records_and_duplicate_ids_fail(self):
        cases = [
            ("advbench", b"target\nmissing goal\n"),
            ("harmbench", csv_bytes([harmbench_row(category="contextual")])),
            ("harmbench", csv_bytes([harmbench_row(), harmbench_row()])),
            (
                "harmbench",
                csv_bytes(
                    [
                        harmbench_row(category="copyright"),
                        harmbench_row(category="copyright"),
                    ]
                ),
            ),
            ("do_not_answer_ja", b"{}"),
            ("do_not_answer_ja", b"[null]"),
        ]
        for name, raw in cases:
            with self.subTest(name=name), self.assertRaises((ValueError, TypeError)):
                parse_source(name, raw)

    def test_dataset_validation_checks_hash_language_ids_and_counts(self):
        fixture = dataset_fixture()
        validate_dataset(fixture)
        for key, value in (
            ("prompt", "Changed text"),
            ("id", "wrong:0001"),
            ("language", "ja"),
            ("category", ""),
        ):
            changed = copy.deepcopy(fixture)
            changed["rows"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_dataset(changed)
        for mutation in ("duplicate", "count", "source", "purpose", "revision"):
            changed = copy.deepcopy(fixture)
            if mutation == "duplicate":
                changed["rows"].append(changed["rows"][0])
            elif mutation == "count":
                changed["sources"][0]["source_count"] = 2
            elif mutation == "source":
                changed["sources"] = []
            elif mutation == "revision":
                changed["sources"][0]["revision"] = "main"
            else:
                changed["purpose"] = "other"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_dataset(changed)

    def test_corrupt_cached_source_is_rejected_without_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dataset.json"
            source_dir = output.parent / "sources"
            source_dir.mkdir()
            spec = SOURCES["advbench"]
            cached = (
                source_dir
                / f"advbench-{spec['revision']}-{Path(spec['filename']).name}"
            )
            cached.write_bytes(b"changed content")
            with (
                patch("httpx.Client.get") as get,
                self.assertRaisesRegex(ValueError, "hash mismatch"),
            ):
                prepare_dataset(output)
            get.assert_not_called()
            self.assertFalse(output.exists())

    def test_source_verification_rejects_rehashed_changes_and_invented_subsets(self):
        raw = csv_bytes(
            [
                {"goal": "Explain a rainbow.", "target": "unused"},
                {"goal": "Describe a cloud.", "target": "unused"},
            ]
        )
        spec: dict = {
            **SOURCES["advbench"],
            "sha256": digest(raw.decode()),
            "source_count": 2,
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(SOURCES, {"advbench": spec}, clear=True),
        ):
            output = Path(directory) / "dataset.json"
            source_dir = output.parent / "sources"
            source_dir.mkdir()
            cached = (
                source_dir
                / f"advbench-{spec['revision']}-{Path(spec['filename']).name}"
            )
            cached.write_bytes(raw)
            with patch("httpx.Client.get") as get:
                dataset = prepare_dataset(output)
                verify_sources(dataset, output)
                reordered = copy.deepcopy(dataset)
                reordered["rows"].reverse()
                verify_sources(reordered, output)
                for mutation in ("prompt", "subset", "stats", "license"):
                    changed = copy.deepcopy(dataset)
                    if mutation == "prompt":
                        changed["rows"][0]["prompt"] = "A different harmless question."
                        changed["rows"][0]["prompt_sha256"] = digest(
                            changed["rows"][0]["prompt"]
                        )
                    elif mutation == "subset":
                        changed["rows"] = changed["rows"][:1]
                        changed["sources"][0].update(source_count=1, included_count=1)
                    elif mutation == "stats":
                        changed["sources"][0]["exclusion_reasons"] = {
                            "invented": "Changed"
                        }
                    else:
                        changed["sources"][0]["license"] = "Invented license"
                    validate_dataset(changed)
                    with (
                        self.subTest(mutation=mutation),
                        self.assertRaisesRegex(ValueError, "[Cc]anonical"),
                    ):
                        verify_sources(changed, output)
                cached.write_bytes(b"modified raw source")
                with self.assertRaisesRegex(ValueError, "Pinned source hash mismatch"):
                    verify_sources(dataset, output)
                cached.unlink()
                with self.assertRaisesRegex(
                    ValueError, "Missing canonical source cache"
                ):
                    verify_sources(dataset, output)
                get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
