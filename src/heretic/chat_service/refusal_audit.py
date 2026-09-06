# SPDX-License-Identifier: AGPL-3.0-or-later
"""Collect fixed-intervention, benign over-refusal evaluations over the chat API.

No attack search or automatic success judging is performed by this collector.
Content review is supplied separately and tied to the exact collected response.
"""

import argparse
import datetime
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from heretic.prompt_lab.data import prompt_key


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def request_json(base_url: str, route: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + route,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def validate_dataset(dataset: dict, context: dict):
    rows = dataset["rows"]
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Empty dataset or duplicate IDs.")
    hashes = {prompt_key(r["prompt"]) for r in rows}
    if len(hashes) != len(rows):
        raise ValueError("Duplicate questions.")
    for row in rows:
        if (
            row.get("source_safety") != "safe"
            or row.get("reviewed_scope") != "benign_overrefusal"
        ):
            raise ValueError("This collector requires reviewed benign inputs.")
    for metadata in context["methods"].values():
        seen = set(metadata.get("selection_data", {}).get("prompt_sha256", []))
        if hashes & seen:
            raise ValueError("Evaluation input overlaps intervention selection data.")


def collect(args):
    dataset_text = args.data.read_text(encoding="utf-8")
    dataset = json.loads(dataset_text)
    context = request_json(args.base_url, "/api/audit-context")
    validate_dataset(dataset, context)
    missing = set(args.methods) - context["methods"].keys()
    if missing:
        raise ValueError(f"Unavailable methods: {sorted(missing)}")
    rows = dataset["rows"]
    if args.ids:
        missing_ids = set(args.ids) - {row["id"] for row in rows}
        if missing_ids:
            raise ValueError(f"Unknown IDs: {sorted(missing_ids)}")
        rows = [row for row in rows if row["id"] in args.ids]
    generation = {
        "max_new_tokens": args.max_new_tokens,
        "do_sample": False,
        "enable_tools": False,
        "enable_thinking": False,
    }
    run: dict
    if args.output.exists():
        run = json.loads(args.output.read_text(encoding="utf-8"))
        if (
            run["dataset_sha256"] != digest(dataset_text)
            or run["context"] != context
            or run["generation"] != generation
        ):
            raise ValueError("Cannot append: dataset, runtime, or generation changed.")
    else:
        run = {
            "schema_version": 1,
            "created_at": utc_now(),
            "dataset": dataset,
            "dataset_sha256": digest(dataset_text),
            "context": context,
            "generation": generation,
            "records": [],
        }
    done = {(r["id"], r["method"], r["repeat"]) for r in run["records"]}
    for row in rows:
        for method in args.methods:
            for repeat in range(1, args.repeats + 1):
                if (row["id"], method, repeat) in done:
                    continue
                payload = {
                    "messages": [{"role": "user", "content": row["prompt"]}],
                    "method": method,
                    "enable_tools": False,
                    "max_new_tokens": args.max_new_tokens,
                }
                started = time.monotonic()
                result = request_json(args.base_url, "/api/chat/complete", payload)
                if (
                    result["context_id"] != context["id"]
                    or result["method"] != method
                    or result["tools"]
                ):
                    raise ValueError("Response context, method, or tools mismatch.")
                run["records"].append(
                    {
                        "id": row["id"],
                        "method": method,
                        "repeat": repeat,
                        "prompt": row["prompt"],
                        "response": result["response"],
                        "response_sha256": digest(result["response"]),
                        "request": payload,
                        "context_id": result["context_id"],
                        "created_at": utc_now(),
                        "wall_seconds": time.monotonic() - started,
                        "model_seconds": result["seconds"],
                    }
                )
                save_json(args.output, run)
                print(f"Collected {row['id']} {method} repeat={repeat}", flush=True)
    if request_json(args.base_url, "/api/audit-context") != context:
        raise ValueError("Runtime changed during collection.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://100.91.77.114:8768")
    parser.add_argument(
        "--data", type=Path, default=Path("examples/refusal_audit/sbi-safe-ja.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("prompt-runs/refusal-ja/collected.json")
    )
    parser.add_argument("--methods", nargs="+", default=["baseline"])
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=1)
    parser.add_argument(
        "--max-new-tokens", type=int, default=512, choices=range(16, 769)
    )
    collect(parser.parse_args())


if __name__ == "__main__":
    main()
