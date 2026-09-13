"""Manage project-scoped HTTP apps independently of SSH and OpenCode tool timeouts."""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TAILSCALE_HOST = "100.91.77.114"


def identity(project, name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("App name must be 1–40 lowercase letters, numbers, or hyphens")
    digest = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:12]
    return f"qwen-app-{digest}-{name}.service"


def state_path(unit):
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    root = root / "breakllm-apps"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / f"{unit}.json"


def call(*args, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=15)


def properties(unit):
    result = call(
        "systemctl",
        "--user",
        "show",
        unit,
        "--property=ActiveState,MainPID,ExecMainStatus",
        check=False,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, urllib.error.URLError):
        return None


def wait_ready(unit, url, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = properties(unit)
        if state.get("ActiveState") in ("failed", "inactive"):
            break
        status = http_status(url)
        if (
            state.get("ActiveState") == "active"
            and status is not None
            and 200 <= status < 400
        ):
            return state, status
        time.sleep(0.4)
    raise ValueError(
        f"App did not become ready: {url}. Inspect qwen-code app logs for the error."
    )


def launch(project, args, command):
    unit = identity(project, args.name)
    record_path = state_path(unit)
    if properties(unit).get("ActiveState") in ("active", "activating"):
        raise ValueError(
            f"{args.name} is already running. Use app status or app stop {args.name} first."
        )
    if not 1024 <= args.port <= 65535:
        raise ValueError("Use an unprivileged TCP port between 1024 and 65535")
    # Fail before launching on an occupied/nonlocal address; do not kill its owner.
    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError as error:
            raise ValueError(f"Cannot bind {args.host}:{args.port}: {error}") from error
    if not args.health_path.startswith("/") or args.health_path.startswith("//"):
        raise ValueError("--health-path must be a path beginning with a single /")
    default_ui = not command
    if default_ui:
        if not (project / "chat-ui/package.json").is_file():
            raise ValueError(
                "No chat-ui/package.json here. Specify -- command for another HTTP app."
            )
        if http_status(args.api_url + "/api/health") != 200:
            raise ValueError(f"API is not reachable: {args.api_url}/api/health")
        command = [
            "npm",
            "--prefix",
            "chat-ui",
            "run",
            "dev",
            "--",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--strictPort",
        ]
    executable = shutil.which(command[0])
    if executable is None:
        raise ValueError(f"Executable not found: {command[0]}")
    command = [executable, *command[1:]]
    base = f"http://{args.host}:{args.port}"
    health_url = base + args.health_path
    record = {
        "name": args.name,
        "directory": str(project),
        "unit": unit,
        "url": base + "/",
        "health_url": health_url,
        "command": command,
        "api_url": args.api_url if default_ui else None,
    }
    # The user manager owns the process, so a shell timeout or disconnect cannot kill it.
    call("systemctl", "--user", "reset-failed", unit, check=False)
    call(
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        f"--working-directory={project}",
        "--property=Type=exec",
        "--property=KillMode=control-group",
        f"--setenv=PATH={os.environ.get('PATH', '')}",
        f"--setenv=HOST={args.host}",
        f"--setenv=PORT={args.port}",
        f"--setenv=BREAKLLM_API_BASE_URL={args.api_url}",
        "--",
        *command,
    )
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    record_path.chmod(0o600)
    try:
        state, status = wait_ready(unit, health_url, args.wait)
        if default_ui and http_status(base + "/api/health") != 200:
            raise ValueError(
                "The UI started, but its API proxy failed. Inspect app logs."
            )
    except ValueError:
        # Failed readiness must not leave an unverified background process behind.
        call("systemctl", "--user", "stop", unit, check=False)
        logs = call(
            "journalctl", "--user", "-u", unit, "-n", "30", "--no-pager", check=False
        )
        print(logs.stdout, file=sys.stderr)
        raise
    print(
        json.dumps(
            {**record, "state": state, "http_status": status, "ready": True},
            ensure_ascii=False,
            indent=2,
        )
    )


def main(arguments=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: qwen-code app start demo --port 8772 -- python3 -m http.server 8772 --bind 100.91.77.114",
    )
    parser.add_argument("action", choices=["start", "status", "logs", "stop"])
    parser.add_argument("name", nargs="?", default="frontend")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--host", default=TAILSCALE_HOST)
    parser.add_argument("--api-url", default=f"http://{TAILSCALE_HOST}:8768")
    parser.add_argument("--health-path", default="/")
    parser.add_argument("--wait", type=float, default=20)
    argv = list(sys.argv[1:] if arguments is None else arguments)
    command = []
    if "--" in argv:
        split = argv.index("--")
        command, argv = argv[split + 1 :], argv[:split]
        if not command:
            parser.error("-- requires an application command")
    args = parser.parse_args(argv)
    if command and args.action != "start":
        parser.error("Only app start accepts a command")
    if not 0 < args.wait <= 60:
        parser.error("--wait must be between 0 and 60 seconds")
    project = Path.cwd().resolve()
    unit = identity(project, args.name)
    if args.action == "start":
        launch(project, args, command)
        return 0
    record_path = state_path(unit)
    if args.action == "logs":
        os.execvp(
            "journalctl", ["journalctl", "--user", "-u", unit, "-n", "60", "--no-pager"]
        )
    if args.action == "stop":
        call("systemctl", "--user", "stop", unit)
        print(f"Stopped: {unit}")
        return 0
    record = (
        json.loads(record_path.read_text()) if record_path.is_file() else {"unit": unit}
    )
    state = properties(unit)
    status = http_status(record["health_url"]) if "health_url" in record else None
    ready = (
        state.get("ActiveState") == "active"
        and status is not None
        and 200 <= status < 400
    )
    if record.get("api_url"):
        ready = ready and http_status(record["url"] + "api/health") == 200
    print(
        json.dumps(
            {**record, "state": state, "http_status": status, "ready": ready},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"qwen-code app: {error}", file=sys.stderr)
        sys.exit(1)
