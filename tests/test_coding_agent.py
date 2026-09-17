"""Regressions for history selection, SSH argument forwarding and app readiness."""

import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

    def test_local_wrapper_tunnel_ignores_config_forwards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            share = root / "share"
            share.mkdir()
            (share / "client.py").symlink_to(DEPLOY / "client.py")
            (share / "config.env").write_text(
                "QWEN_CODE_HOST=gpu\nQWEN_CODE_PORT=18787\nQWEN_CODE_SERVER_DIR=/srv\n"
            )
            log = root / "ssh.log"
            stub = root / "ssh"
            stub.write_text(
                "#!/usr/bin/env python3\nimport sys,json\n"
                f"open({str(log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
                "sys.exit(1 if 'check' in sys.argv else 0)\n"
            )
            stub.chmod(0o755)
            project = root / "project"
            project.mkdir()
            result = subprocess.run(
                ["bash", str(DEPLOY / "local/qwen-code"), "--dry-run"],
                cwd=project,
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "QWEN_CODE_HOME": str(share),
                    "TMPDIR": str(root),
                    "XDG_DATA_HOME": str(root / "data"),
                },
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                json.loads(result.stdout)["directory"], str(project.resolve())
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            master = next(c for c in calls if "ControlMaster=yes" in c)
            self.assertIn("ClearAllForwardings=yes", master)
            self.assertNotIn("-L", master)
            forward = next(c for c in calls if "forward" in c)
            self.assertEqual(forward[:2], ["-F", "/dev/null"])
            self.assertEqual(
                forward[forward.index("-L") + 1], "127.0.0.1:18787:127.0.0.1:8787"
            )
            self.assertEqual(forward[-1], "gpu")

    def test_local_client_uses_tunnel_api_and_refuses_server_apps(self):
        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        api = f"http://127.0.0.1:{server.server_address[1]}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "opencode"
            fake.write_text(
                "#!/usr/bin/env python3\nimport os,json\nprint(json.dumps([json.loads(os.environ['OPENCODE_CONFIG_CONTENT']), os.environ.get('OPENCODE_DISABLE_CLAUDE_CODE')]))\n"
            )
            fake.chmod(0o755)
            runtime = root / "RUNTIME.md"
            runtime.write_text("local")
            env = {
                **os.environ,
                "QWEN_CODE_LOCAL": "1",
                "QWEN_CODE_API": api,
                "QWEN_CODE_OPENCODE": str(fake),
                "QWEN_CODE_RUNTIME": str(runtime),
                "XDG_DATA_HOME": str(root / "data"),
            }
            env.pop("OPENCODE_DISABLE_CLAUDE_CODE", None)
            client_py = str(DEPLOY / "client.py")
            result = subprocess.run(
                [sys.executable, client_py, "run", "hello"],
                cwd=directory,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            config, claude_disabled = json.loads(result.stdout)
            self.assertEqual(claude_disabled, "1")
            self.assertEqual(
                config["provider"]["hb-gpu-0"]["options"]["baseURL"], f"{api}/v1"
            )
            self.assertIn(str(runtime), config["instructions"])
            refused = subprocess.run(
                [sys.executable, client_py, "app", "start"],
                cwd=directory,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(refused.returncode, 1)
            self.assertIn("hb-gpu-0", refused.stderr)


if __name__ == "__main__":
    unittest.main()
