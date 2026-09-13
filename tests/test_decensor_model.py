# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "decensor_model", ROOT / "scripts" / "decensor_model.py"
)
decensor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decensor)

from heretic.chat_service.registry import load_catalog


class DecensorModelTests(unittest.TestCase):
    def test_command_is_non_interactive_and_exports_a_merge(self):
        command = decensor.heretic_command(
            "Qwen/Qwen3-1.7B", Path("/w"), n_trials=80, seed=7, trial_index=2
        )
        joined = " ".join(command)
        self.assertEqual(command[0], "heretic")
        # Every prompt-gating setting is supplied, so no interactive question remains.
        for flag in ("--model-action save", "--export-strategy merge", "--checkpoint-action continue",
                     "--trial-index 2", "--save-directory /w", "--n-trials 80", "--seed 7"):
            self.assertIn(flag, joined)

    def test_command_omits_seed_when_unset(self):
        self.assertNotIn("--seed", decensor.heretic_command(
            "m", Path("/w"), n_trials=10, seed=None, trial_index=0
        ))

    def test_save_directory_matches_registry_model_folder(self):
        # Must line up with registry.artifact_root_for's per-model slug.
        path = decensor.save_directory(Path("/r"), "Qwen/Qwen3-1.7B")
        self.assertEqual(path, Path("/r/models/Qwen__Qwen3-1.7B/weights"))

    def test_register_writes_catalog_the_service_can_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "models.json"
            decensor.register_model(catalog_path, "/w/model", "M (検閲除去)")
            catalog = load_catalog(catalog_path)
            self.assertEqual(catalog["/w/model"], {"label": "M (検閲除去)", "decensored": True})

    def test_register_replaces_an_existing_entry_for_the_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "models.json"
            decensor.register_model(catalog_path, "/w/model", "old")
            decensor.register_model(catalog_path, "/w/model", "new")
            entries = json.loads(catalog_path.read_text())
            self.assertEqual([e["id"] for e in entries], ["/w/model"])
            self.assertEqual(entries[0]["label"], "new")


if __name__ == "__main__":
    unittest.main()
