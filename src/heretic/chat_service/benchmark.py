# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
import datetime
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import torch
import transformers

from heretic.prompt_lab.data import Example, prompt_key, write_json
from heretic.prompt_lab.engine import assert_unseen, provenance, save_artifact

from . import optimizers
from .report import judge, outcome, write_html
from .runtime import JAPANESE_SYSTEM, Runtime


def main():
    parser = argparse.ArgumentParser(
        description="Train bounded interventions and report all held-out comparisons."
    )
    parser.add_argument("--model", default="Qwen/Qwen3-1.7B")
    parser.add_argument(
        "--data", type=Path, default=Path("examples/prompt_lab/comparison-ja.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("prompt-runs/comparison-ja")
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--system", default=JAPANESE_SYSTEM)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--gcg-steps", type=int, default=12)
    parser.add_argument("--search-rounds", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument(
        "--methods", nargs="+", default=["soft", "mse", "gcg", "pair", "autodan"]
    )
    args = parser.parse_args()
    rows = json.loads(args.data.read_text(encoding="utf-8"))
    if len({row["id"] for row in rows}) != len(rows) or len(
        {prompt_key(row["prompt"]) for row in rows}
    ) != len(rows):
        raise ValueError("Duplicate example IDs or prompts across splits.")
    train = [
        Example(**{k: row[k] for k in ("id", "prompt", "target", "split")})
        for row in rows
        if row["split"] == "train"
    ]
    validation = [
        Example(**{k: row[k] for k in ("id", "prompt", "target", "split")})
        for row in rows
        if row["split"] == "validation"
    ]
    test = [row for row in rows if row["split"] == "test"]
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output / "methods"
    runtime = Runtime(args.model, root, args.device, system=args.system)
    for method in args.methods:
        if method in runtime.methods:
            print(f"Reusing verified artifact {method}", flush=True)
            continue
        started = time.monotonic()
        print(f"START {method}", flush=True)
        if method == "soft":
            template, weights, extra = optimizers.soft_nll(
                runtime, train, validation, epochs=args.epochs
            )
        elif method == "mse":
            template, weights, extra = optimizers.mse_steering(
                runtime, rows, train, validation, epochs=args.epochs
            )
        elif method == "gcg":
            template, weights, extra = optimizers.gcg(
                runtime, train, validation, steps=args.gcg_steps
            )
        elif method in {"pair", "autodan"}:
            template, weights, extra = optimizers.natural_search(
                runtime, train, validation, method=method, rounds=args.search_rounds
            )
        else:
            raise ValueError(f"Unknown method {method}")
        metadata = {
            "kind": "soft" if weights is not None else "text",
            "method": method,
            "engine": runtime.engine.identity(),
            "selection_data": provenance(train + validation),
            "template": asdict(template),
            "training_seconds": time.monotonic() - started,
            **extra,
        }
        save_artifact(root / method, metadata, weights)
        runtime.methods[method] = (template, weights)
        runtime.metadata[method] = metadata
        print(f"SAVED {method}", flush=True)
    report = {
        "model": args.model,
        "model_revision": runtime.engine.identity()["resolved_commit"],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "generation": {
            "do_sample": False,
            "enable_thinking": False,
            "max_new_tokens": args.max_new_tokens,
            "tools": False,
            "system_prompt": args.system,
        },
        "environment": {
            "torch": str(torch.__version__),
            "transformers": transformers.__version__,
            "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
        },
        "dataset_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "evaluation_note": "All held-out rows; task-specific keyword rubric, not harmful-request ASR. No external guardrail tested. Compact single-model adaptations.",
        "methods": runtime.metadata,
        "comparisons": [],
        "summary": {},
        "reference_responses": [],
    }
    for row in [r for r in rows if r["split"] == "train"]:
        response = runtime.complete(
            [{"role": "user", "content": row["reference_prompt"]}],
            "baseline",
            max_new_tokens=160,
        )
        report["reference_responses"].append(
            {
                "id": row["id"],
                "prompt": row["reference_prompt"],
                "response": response,
                "judgment": judge(response, row),
            }
        )
    for row in test:
        before = runtime.complete(
            [{"role": "user", "content": row["prompt"]}],
            "baseline",
            max_new_tokens=args.max_new_tokens,
        )
        before_judgment = judge(before, row)
        print(f"BASELINE {row['id']}: {before[:160]}", flush=True)
        for method in args.methods:
            example = Example(
                **{k: row[k] for k in ("id", "prompt", "target", "split")}
            )
            assert_unseen([example], runtime.metadata[method])
            started = time.monotonic()
            after = runtime.complete(
                [{"role": "user", "content": row["prompt"]}],
                method,
                max_new_tokens=args.max_new_tokens,
            )
            after_judgment = judge(after, row)
            comparison = {
                "id": row["id"],
                "category": row["category"],
                "prompt": row["prompt"],
                "target": row["target"],
                "required_any": row["required_any"],
                "method": method,
                "before": before,
                "after": after,
                "before_judgment": before_judgment,
                "after_judgment": after_judgment,
                "outcome": outcome(before_judgment, after_judgment),
                "seconds": round(time.monotonic() - started, 3),
                "template": asdict(runtime.methods[method][0]),
            }
            report["comparisons"].append(comparison)
            print(
                f"RESULT {row['id']} {method} {comparison['outcome']}: {after[:160]}",
                flush=True,
            )
            report["summary"] = {
                m: dict(
                    Counter(
                        r["outcome"] for r in report["comparisons"] if r["method"] == m
                    )
                )
                for m in args.methods
            }
            write_json(args.output / "comparison.json", report)
            write_html(report, args.output / "comparison.html")
    print(json.dumps(report["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
