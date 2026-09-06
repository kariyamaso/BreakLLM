"""Exercise streaming tool calls and a real OpenCode file-edit/test cycle."""

import argparse
import datetime
import json
import os
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path


def post(base_url, payload):
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=300)


def verify_api(base_url):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_workspace_fact",
                "description": "Read a fact stored in the current workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": ["verification_code"]}
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [
        {
            "role": "system",
            "content": "Use tools when requested. Keep answers concise.",
        },
        {
            "role": "user",
            "content": "Read the workspace verification_code with read_workspace_fact, then return the exact code.",
        },
    ]
    payload = {
        "model": "qwen3.8-27b-turbo",
        "messages": messages,
        "tools": tools,
        "tool_choice": "required",
        "temperature": 0,
        "max_tokens": 2048,
        "stream": True,
    }
    calls = {}
    content = ""
    reasoning = ""
    finish = None
    started = time.monotonic()
    with post(base_url, payload) as response:
        for line in response:
            if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                continue
            event = json.loads(line[6:])
            if "error" in event:
                raise RuntimeError(event["error"])
            for choice in event.get("choices", []):
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta", {})
                content += delta.get("content") or ""
                reasoning += delta.get("reasoning_content") or ""
                for part in delta.get("tool_calls", []):
                    call = calls.setdefault(
                        part["index"],
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if part.get("id"):
                        call["id"] = part["id"]
                    for key in ("name", "arguments"):
                        call["function"][key] += part.get("function", {}).get(key) or ""
    assert finish == "tool_calls", (finish, content, reasoning)
    assert len(calls) == 1, calls
    call = next(iter(calls.values()))
    assert call["id"] and call["function"]["name"] == "read_workspace_fact", call
    assert json.loads(call["function"]["arguments"]) == {"name": "verification_code"}, (
        call
    )
    nonce = secrets.token_hex(12)
    messages.extend(
        [
            {
                "role": "assistant",
                "content": content or None,
                "reasoning_content": reasoning,
                "tool_calls": [call],
            },
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps({"verification_code": nonce}),
            },
        ]
    )
    payload.update(stream=False, tool_choice="none")
    with post(base_url, payload) as response:
        result = json.load(response)
    final = result["choices"][0]["message"].get("content") or ""
    assert nonce in final, result
    return {
        "passed": True,
        "seconds": round(time.monotonic() - started, 2),
        "tool_call": call,
        "final": final,
        "usage": result.get("usage"),
        "timings": result.get("timings"),
    }


def verify_agent(deployment_dir, output_dir):
    workspace = output_dir / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    source = workspace / "stats.py"
    source.write_text(
        "def mean(values):\n"
        '    """Return the arithmetic mean. Empty input raises ValueError."""\n'
        "    return sum(values)\n"
    )
    test = workspace / "test_stats.py"
    test.write_text(
        "import unittest\nfrom stats import mean\n\n"
        "class MeanTests(unittest.TestCase):\n"
        "    def test_positive(self):\n        self.assertEqual(mean([2, 4, 6]), 4)\n"
        "    def test_negative(self):\n        self.assertEqual(mean([-3, -1]), -2)\n"
        "    def test_fractional(self):\n        self.assertAlmostEqual(mean([1, 2]), 1.5)\n"
        "    def test_zero(self):\n        self.assertEqual(mean([0, 0]), 0)\n"
        "    def test_empty(self):\n        with self.assertRaises(ValueError):\n            mean([])\n"
    )
    original_test = test.read_bytes()
    before = subprocess.run(
        ["python3", "-m", "unittest", "-v"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode != 0, "Fixture should fail before the agent edits it"
    config = json.loads((deployment_dir / "opencode.json").read_text())
    # Only the isolated fixture and its test runner are needed for this check.
    config["permission"] = {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "bash": {"*": "deny", "python3 -m unittest*": "allow"},
    }
    config["agent"]["build"]["steps"] = 12
    env = dict(os.environ, OPENCODE_CONFIG_CONTENT=json.dumps(config))
    runtime = deployment_dir.parent.parent / ".coding-agent"
    prompt = (
        "この小さなPythonプロジェクトを確認し、stats.py の mean を修正してください。"
        "算術平均を返し、空の入力には ValueError を送出する仕様です。"
        "テストファイルを読んでから実装し、python3 -m unittest -v を実行して確認してください。"
        "変更してよいファイルは stats.py だけです。最後に結果を日本語で報告してください。"
    )
    started = time.monotonic()
    with (
        (output_dir / "opencode-events.jsonl").open("w") as stdout,
        (output_dir / "opencode-stderr.log").open("w") as stderr,
    ):
        completed = subprocess.run(
            [
                str(runtime / "bin/opencode"),
                "run",
                "--dir",
                str(workspace),
                "--format",
                "json",
                "--title",
                "Qwen local coding smoke test",
                prompt,
            ],
            env=env,
            stdout=stdout,
            stderr=stderr,
            timeout=900,
            check=False,
        )
    assert completed.returncode == 0, "OpenCode failed; inspect opencode-stderr.log"
    events = [
        json.loads(line)
        for line in (output_dir / "opencode-events.jsonl").read_text().splitlines()
        if line.startswith("{")
    ]
    assert not any(event.get("type") == "error" for event in events), events
    used_tools = [
        event.get("part", {}).get("tool")
        for event in events
        if event.get("type") == "tool_use"
    ]
    assert "bash" in used_tools, ("Agent did not run the tests", used_tools)
    assert any(tool in used_tools for tool in ("edit", "write", "apply_patch")), (
        used_tools
    )
    assert test.read_bytes() == original_test, "Agent changed the tests"
    after = subprocess.run(
        ["python3", "-m", "unittest", "-v"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    (output_dir / "unittest.log").write_text(after.stdout + after.stderr)
    assert after.returncode == 0, after.stdout + after.stderr
    return {
        "passed": True,
        "seconds": round(time.monotonic() - started, 2),
        "workspace": str(workspace),
        "tools": used_tools,
        "tests": 5,
        "implementation": source.read_text(),
        "test_output": after.stdout + after.stderr,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8787/v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        report["api"] = verify_api(args.api_url)
        print("Streaming tool call and tool-result round trip: PASS", flush=True)
        report["agent"] = verify_agent(Path(__file__).resolve().parent, args.output)
        print("OpenCode read/edit/test cycle: PASS", flush=True)
    finally:
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
