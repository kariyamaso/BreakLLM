"""OpenCode entry point with project-scoped history and offline administration."""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parent.parent
# A local install (local/install.sh) reaches the server model through an SSH tunnel.
LOCAL = os.environ.get("QWEN_CODE_LOCAL") == "1"
API = os.environ.get("QWEN_CODE_API", "http://127.0.0.1:8787").rstrip("/")
OPENCODE = Path(os.environ.get("QWEN_CODE_OPENCODE", ROOT / ".coding-agent/bin/opencode"))
RUNTIME = Path(os.environ.get("QWEN_CODE_RUNTIME", DEPLOY / "RUNTIME.md"))
HELP = """qwen-code [--project REMOTE_DIRECTORY] [options or command]
  (no command)          Resume this directory's latest saved conversation
  --new                 Start a new conversation
  --session SESSION_ID  Resume a specific saved conversation
  history [--json]      List this directory's saved conversations (no GPU needed)
  app start             Start the development UI and verify its URL (server only)
  app status|logs|stop   Inspect or stop that project's development UI
  app --help            Launch other HTTP apps as managed processes
  run MESSAGE           Run a new noninteractive OpenCode task
  --dry-run             Show directory/session/arguments without launching
Other OpenCode commands and flags are forwarded unchanged.
"""


def database_path():
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data / "opencode/opencode.db"


def sessions(directory, db_path=None):
    """Read only; do not create, migrate, or modify the user's history."""
    path = db_path or database_path()
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, title, directory, time_updated FROM session "
            "WHERE directory=? AND parent_id IS NULL AND time_archived IS NULL "
            "ORDER BY time_updated DESC, id DESC",
            (str(directory),),
        )
        return [dict(row) for row in rows]


def prepare(arguments, cwd):
    args = list(arguments)
    directory = Path(cwd)
    if args[:1] == ["--project"]:
        if len(args) < 2:
            raise ValueError("--project requires a remote directory")
        directory = Path(args[1]).expanduser()
        args = args[2:]
    elif args and args[0].startswith(("/", "./", "../")):
        directory = Path(args.pop(0)).expanduser()
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    dry_run = "--dry-run" in args
    args = [arg for arg in args if arg != "--dry-run"]
    new = "--new" in args
    args = [arg for arg in args if arg != "--new"]
    interactive = not args or args[0].startswith("-")
    explicit = any(
        arg in ("-s", "--session") or arg.startswith("--session=") for arg in args
    )
    continuing = any(arg in ("-c", "--continue") for arg in args)
    if new and (explicit or continuing):
        raise ValueError("--new cannot be combined with --session or --continue")
    informational = any(arg in ("-h", "--help", "-v", "--version") for arg in args)
    if not informational and (
        (interactive and not new and not explicit) or (continuing and not explicit)
    ):
        args = [arg for arg in args if arg not in ("-c", "--continue")]
        history = sessions(directory)
        if history:
            args.extend(["--session", history[0]["id"]])
    return directory, args, dry_run, interactive


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments == ["--help"] or arguments == ["-h"]:
        print(HELP)
        return 0
    directory, args, dry_run, interactive = prepare(arguments, Path.cwd())
    if args in (["--help"], ["-h"]):
        print(HELP)
        return 0
    if args[:1] == ["history"]:
        rows = sessions(directory)
        if "--json" in args:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(f"履歴: {directory}\n保存先: {database_path()}")
            for row in rows:
                print(f"{row['id']}  {row['title']}")
            if not rows:
                print("このディレクトリの履歴はまだありません。")
        return 0
    if args[:1] == ["app"]:
        if LOCAL:
            raise ValueError("app commands run on hb-gpu-0: ssh hb-gpu-0, then qwen-code app")
        os.chdir(directory)
        os.execv(sys.executable, [sys.executable, str(DEPLOY / "apps.py"), *args[1:]])
    if dry_run:
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "arguments": args,
                    "database": str(database_path()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    binary = OPENCODE
    if not binary.is_file():
        raise ValueError(f"OpenCode binary not installed: {binary}")
    # Administrative commands remain usable while the GPU model is stopped.
    if (
        interactive and not any(a in args for a in ("--help", "-h", "--version", "-v"))
    ) or args[:1] == ["run"]:
        try:
            with urllib.request.urlopen(
                f"{API}/health", timeout=3
            ) as response:
                if json.load(response).get("status") != "ok":
                    raise ValueError("Qwen model is not ready")
        except (OSError, urllib.error.URLError, ValueError) as error:
            hint = (
                f"Check the SSH tunnel to hb-gpu-0 ({API}); if it is up, ask the server admin."
                if LOCAL
                else "Run: systemctl --user start breakllm-coding-model.service."
            )
            raise ValueError(
                f"Qwen model unavailable. {hint} History commands remain available."
            ) from error
    config = json.loads((DEPLOY / "opencode.json").read_text())
    config["provider"]["hb-gpu-0"]["options"]["baseURL"] = f"{API}/v1"
    config.setdefault("instructions", []).append(str(RUNTIME))
    config.setdefault("plugin", []).append((DEPLOY / "context-guard.mjs").as_uri())
    os.environ["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    # ~/.claude/CLAUDE.md and skills can exceed the 16K compaction threshold on their
    # own, which makes OpenCode compact after every tool call and never finish.
    os.environ.setdefault("OPENCODE_DISABLE_CLAUDE_CODE", "1")
    os.environ.setdefault("OPENCODE_DISABLE_EXTERNAL_SKILLS", "1")
    os.chdir(directory)
    if interactive:
        print(
            f"作業先: {directory}\n履歴: qwen-code history / 新規: qwen-code --new"
            + ("" if LOCAL else "\nアプリ: qwen-code app start"),
            file=sys.stderr,
        )
    os.execv(binary, [str(binary), *args])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"qwen-code: {error}", file=sys.stderr)
        sys.exit(1)
