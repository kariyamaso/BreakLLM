#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Weight-level decensoring: run Heretic's abliteration on a Hugging Face model,
export a merged checkpoint, and register it so the chat service serves it.

This is the weight layer of the pipeline. It rewrites the model's weights with
Heretic's public abliteration method (Arditi et al. 2024), producing a standalone
decensored checkpoint. The chat service's prompt-level escalation (soft / mse /
gcg / pair / autodan) then runs on top of whichever model is selected, including
one produced here.

Heretic itself does the tuning; this wrapper only drives it non-interactively and
records the result in the models.json catalog that the service reads at startup.
Actually running the abliteration needs a GPU, the model weights, and Heretic's
datasets, so the study is delegated to the real `heretic` entry point rather than
reimplemented here.

    # Produce a decensored merge of a model and register it:
    python scripts/decensor_model.py --model Qwen/Qwen3-1.7B --n-trials 120

    # See exactly what would run and be registered, without doing it:
    python scripts/decensor_model.py --model Qwen/Qwen3-1.7B --dry-run
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = ROOT / "prompt-runs/comparison-ja"


def model_slug(model_id: str) -> str:
    """Filesystem-safe slug matching registry.artifact_root_for's model folders."""
    return model_id.replace("/", "__")


def save_directory(report_root: Path, model_id: str) -> Path:
    """Where the merged decensored weights are written for this model."""
    return report_root / "models" / model_slug(model_id) / "weights"


def heretic_command(
    model_id: str, save_dir: Path, *, n_trials: int, seed: int | None, trial_index: int
) -> list[str]:
    """Build the non-interactive Heretic invocation.

    Heretic reads every setting from CLI flags (kebab-case), so setting the export
    controls here skips its interactive prompts: it runs the study, picks the
    trial at ``trial_index`` on the Pareto front, merges the abliteration, and
    saves the standalone model to ``save_dir``."""
    command = [
        "heretic",
        "--model", model_id,
        "--n-trials", str(n_trials),
        "--checkpoint-action", "continue",  # resume a partial study rather than prompt
        "--trial-index", str(trial_index),  # pick a Pareto-front trial without prompting
        "--model-action", "save",            # save locally (not upload) without prompting
        "--export-strategy", "merge",        # a standalone checkpoint, not a bare adapter
        "--save-directory", str(save_dir),
    ]
    if seed is not None:
        command += ["--seed", str(seed)]
    return command


def register_model(
    catalog_path: Path, model_id: str, label: str, *, decensored: bool = True
) -> list[dict]:
    """Add or update an entry in the models.json catalog and write it back.

    ``model_id`` here is the served id the runtime loads: a local weights path for
    a decensored export. Returns the full catalog after the update."""
    try:
        entries = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, ValueError):
        entries = []
    entries = [e for e in entries if not (isinstance(e, dict) and e.get("id") == model_id)]
    entries.append({"id": model_id, "label": label, "decensored": decensored})
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="Hugging Face model id to decensor.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT,
                        help="Chat service report root; holds models/ and models.json.")
    parser.add_argument("--n-trials", type=int, default=120,
                        help="Heretic abliteration trials (more = better Pareto front).")
    parser.add_argument("--seed", type=int, default=None, help="Seed for a reproducible study.")
    parser.add_argument("--trial-index", type=int, default=0,
                        help="Index into the sorted Pareto front of the trial to export.")
    parser.add_argument("--label", default=None,
                        help="Display label in the model picker (default: <name> (検閲除去)).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the command and catalog entry without running or writing.")
    args = parser.parse_args(argv)

    save_dir = save_directory(args.report_root, args.model)
    catalog_path = args.report_root / "models.json"
    label = args.label or f"{args.model.rstrip('/').split('/')[-1]} (検閲除去)"
    command = heretic_command(
        args.model, save_dir, n_trials=args.n_trials, seed=args.seed, trial_index=args.trial_index
    )

    print("Heretic command:\n  " + " ".join(command), flush=True)
    print(f"Served id (local weights): {save_dir}", flush=True)
    print(f"Catalog: {catalog_path}  label={label!r}", flush=True)
    if args.dry_run:
        print("Dry run: nothing was executed or written.", flush=True)
        return 0

    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print(f"Heretic exited with status {result.returncode}; not registering.", file=sys.stderr)
        return result.returncode
    if not (save_dir / "config.json").is_file():
        print(f"No model found at {save_dir}; not registering.", file=sys.stderr)
        return 1
    register_model(catalog_path, str(save_dir), label, decensored=True)
    print(f"Registered decensored model. Restart the chat service to serve it.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
