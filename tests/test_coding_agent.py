"""Regressions for history selection, SSH argument forwarding and app readiness."""

import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/coding-agent"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load("client")
apps = load("apps")


class CodingAgentTests(unittest.TestCase):
    def test_connect_preserves_project_and_all_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "ssh"
            stub.write_text(
                "#!/usr/bin/env python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n"
            )
            stub.chmod(0o755)
            project = "/remote/a project's $(touch NEVER_CREATED)"
            message = "改行\n apostrophe's; $(touch NEVER_CREATED) `pwd`"
            result = subprocess.run(
                [
                    "bash",
                    str(DEPLOY / "connect.sh"),
                    project,
                    "run",
                    "--session",
                    "ses_keep",
                    message,
                ],
                cwd=directory,
                env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"]},
                text=True,
                capture_output=True,
                check=True,
            )
            argv = json.loads(result.stdout)
            self.assertIn("-T", argv)
            self.assertEqual(
                shlex.split(argv[-1]),
                [
                    "/home/ubuntu/.local/bin/qwen-code",
                    "--project",
                    project,
                    "run",
                    "--session",
                    "ses_keep",
                    message,
                ],
            )
            self.assertFalse((root / "NEVER_CREATED").exists())

    def test_history_is_scoped_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "history.db"
            with sqlite3.connect(database) as db:
                db.execute(
                    "CREATE TABLE session(id TEXT,title TEXT,directory TEXT,parent_id TEXT,time_archived INT,time_updated INT)"
                )
                db.executemany(
                    "INSERT INTO session VALUES(?,?,?,?,?,?)",
                    [
                        ("old", "old", str(root), None, None, 1),
                        ("latest", "latest", str(root), None, None, 2),
                        ("other", "other clone", str(root / "other"), None, None, 9),
                        ("child", "subagent", str(root), "latest", None, 10),
                        ("archived", "archived", str(root), None, 1, 11),
                    ],
                )
            before = database.read_bytes()
            self.assertEqual(
                [r["id"] for r in client.sessions(root, database)], ["latest", "old"]
            )
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(client.sessions(root, root / "missing.db"), [])
            self.assertFalse((root / "missing.db").exists())

    def test_default_resume_new_and_explicit_session(self):
        with patch.object(client, "sessions", return_value=[{"id": "ses_latest"}]):
            self.assertEqual(client.prepare([], ROOT)[1], ["--session", "ses_latest"])
            self.assertEqual(client.prepare(["--new"], ROOT)[1], [])
            self.assertEqual(
                client.prepare(["--session", "ses_chosen", "--fork"], ROOT)[1],
                ["--session", "ses_chosen", "--fork"],
            )
            self.assertEqual(
                client.prepare(["run", "hello"], ROOT)[1], ["run", "hello"]
            )
            self.assertEqual(
                client.prepare(["run", "--continue", "hello"], ROOT)[1],
                ["run", "hello", "--session", "ses_latest"],
            )
            with self.assertRaises(ValueError):
                client.prepare(["--new", "--continue"], ROOT)

    def test_managed_apps_cannot_target_another_project(self):
        self.assertNotEqual(
            apps.identity(ROOT, "frontend"), apps.identity(ROOT / "other", "frontend")
        )
        for name in ["../other", "a.service;kill", "", "x/y"]:
            with self.assertRaises(ValueError):
                apps.identity(ROOT, name)

    def test_http_failure_or_dead_process_is_not_ready(self):
        with (
            patch.object(apps, "properties", return_value={"ActiveState": "failed"}),
            patch.object(apps, "http_status", return_value=200),
            self.assertRaises(ValueError),
        ):
            apps.wait_ready("app", "http://example.invalid", 0.1)
        with (
            patch.object(apps, "properties", return_value={"ActiveState": "active"}),
            patch.object(apps, "http_status", return_value=500),
            patch.object(apps.time, "sleep"),
            self.assertRaises(ValueError),
        ):
            apps.wait_ready("app", "http://example.invalid", 0.01)

    def test_compaction_leaves_space_for_next_tool_turn(self):
        config = json.loads((DEPLOY / "opencode.json").read_text())
        limit = config["provider"]["hb-gpu-0"]["models"]["qwen3.8-27b-turbo"]["limit"]
        threshold = limit["input"] - config["compaction"]["reserved"]
        self.assertLessEqual(threshold, 16384)
        self.assertLessEqual(threshold + limit["output"] + 6000, limit["context"])


if __name__ == "__main__":
    unittest.main()
