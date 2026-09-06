# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command-line interface for reproducible prompt experiments."""

import argparse
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import cast

from .data import (
    Template,
    dataset_hash,
    read_examples,
    read_templates,
    select_split,
    write_json,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Optimize prompts with a frozen local causal LM."
    )
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("search", "train", "evaluate", "generate"):
        sub = commands.add_parser(command)
        sub.add_argument("--device", default="auto", help="auto, cpu, cuda[:N], or mps")
        sub.add_argument(
            "--dtype", choices=["float32", "float16", "bfloat16"], default="float32"
        )
        sub.add_argument("--local-files-only", action="store_true")
        if command in {"search", "train"}:
            sub.add_argument("--model", required=True)
            sub.add_argument("--revision")
            sub.add_argument("--system", default="")
            sub.add_argument("--max-length", type=int, default=2048)
            sub.add_argument("--plain", action="store_true")
        else:
            sub.add_argument("--artifact", type=Path, required=True)
        if command != "generate":
            sub.add_argument("--data", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
        if command in {"evaluate", "generate"}:
            sub.add_argument("--max-new-tokens", type=int, default=128)
        if command == "search":
            sub.add_argument("--templates", type=Path, required=True)
        if command == "train":
            sub.add_argument("--tokens", type=int, default=16)
            sub.add_argument("--epochs", type=int, default=10)
            sub.add_argument("--learning-rate", type=float, default=0.01)
            sub.add_argument(
                "--batch-size", type=int, default=4, help="Gradient accumulation size"
            )
            sub.add_argument("--seed", type=int, default=0)
            sub.add_argument(
                "--init-text", default="Please answer the question clearly."
            )
            sub.add_argument("--template-artifact", type=Path)
        if command == "generate":
            sub.add_argument("--prompt", required=True)
    render = commands.add_parser(
        "render", help="Render a text artifact without loading a model"
    )
    render.add_argument("--artifact", type=Path, required=True)
    render.add_argument("--prompt", required=True)
    return root


def run(args: argparse.Namespace) -> None:
    if args.command == "render":
        metadata = json.loads(
            (args.artifact / "artifact.json").read_text(encoding="utf-8")
        )
        if metadata.get("format_version") != 1 or metadata.get("kind") != "text":
            raise ValueError(
                "render requires a text artifact; soft prompts are not text tokens."
            )
        print(Template(**metadata["template"]).render(args.prompt))
        return

    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from .engine import (
        PromptEngine,
        TrainConfig,
        assert_unseen,
        evaluate,
        load_artifact,
        provenance,
        save_artifact,
        search_templates,
        train_soft_prompt,
    )

    if getattr(args, "output", None) is not None and args.output.exists():
        raise ValueError(
            f"Output already exists: {args.output}. Choose a new experiment directory."
        )
    if getattr(args, "max_new_tokens", 1) < 1:
        raise ValueError("max_new_tokens must be positive.")
    examples = read_examples(args.data) if hasattr(args, "data") else []
    train_config = None
    templates = None
    if args.command == "train":
        train_config = TrainConfig(
            tokens=args.tokens,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            batch_size=args.batch_size,
            seed=args.seed,
            init_text=args.init_text,
        )
        select_split(examples, "train")
        select_split(examples, "validation")
        torch.manual_seed(args.seed)
    if args.command == "search":
        templates = read_templates(args.templates)
        select_split(examples, "validation")
    artifact = None
    soft = None
    template = Template()
    artifact_path = getattr(args, "artifact", None) or getattr(
        args, "template_artifact", None
    )
    if artifact_path is not None:
        artifact, soft = load_artifact(artifact_path)
        template = Template(**artifact["template"])
        if args.command == "train" and artifact["kind"] != "text":
            raise ValueError(
                "--template-artifact requires an artifact produced by search."
            )
    if args.command == "evaluate":
        if artifact is None:
            raise ValueError("Evaluation requires an artifact.")
        assert_unseen(select_split(examples, "test"), artifact)
    if args.command in {"evaluate", "generate"}:
        if artifact is None:
            raise ValueError("An artifact is required.")
        settings = artifact["engine"]
    else:
        settings = {
            "model_id": args.model,
            "revision": args.revision,
            "system": args.system,
            "max_length": args.max_length,
            "plain": args.plain,
        }
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu" and torch.backends.mps.is_available():
            device = "mps"
    revision = settings.get("resolved_commit") or settings["revision"]
    kwargs = {"local_files_only": args.local_files_only, "trust_remote_code": False}
    if revision is not None:
        kwargs["revision"] = revision
    print(f"Loading {settings['model_id']} on {device} ({args.dtype})", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(settings["model_id"], **kwargs)
    if tokenizer is None:
        raise ValueError("The model did not provide a text tokenizer.")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = cast(
        torch.nn.Module,
        AutoModelForCausalLM.from_pretrained(
            settings["model_id"], dtype=getattr(torch, args.dtype), **kwargs
        ),
    ).to(device)
    engine = PromptEngine(
        model,
        tokenizer,
        model_id=settings["model_id"],
        revision=settings["revision"],
        system=settings["system"],
        max_length=settings["max_length"],
        plain=settings["plain"],
    )
    if artifact is not None and artifact["engine"] != engine.identity():
        raise ValueError("Artifact model/tokenizer/chat settings differ from this run.")
    environment = {
        "python": platform.python_version(),
        "torch": str(torch.__version__),
        "transformers": transformers.__version__,
        "device": device,
        "dtype": args.dtype,
    }
    if args.command == "generate":
        print(engine.generate(args.prompt, template, soft, args.max_new_tokens))
        return
    if args.command == "evaluate":
        report = evaluate(
            engine, select_split(examples, "test"), template, soft, args.max_new_tokens
        )
        report.update(
            {
                "artifact": str(args.artifact.resolve()),
                "engine": engine.identity(),
                "environment": environment,
                "test_data_sha256": dataset_hash(select_split(examples, "test")),
                "generation": {
                    "max_new_tokens": args.max_new_tokens,
                    "do_sample": False,
                },
                "metric_note": "Target NLL and exact match are not jailbreak success rates. "
                "Guardrail and behavior judgments are unmeasured (null).",
            }
        )
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "report.json", report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return
    validation = select_split(examples, "validation")
    selected_data = validation
    if args.command == "search":
        if templates is None:
            raise ValueError("Templates are required.")
        template, history = search_templates(engine, validation, templates)
        kind = "text"
    else:
        if train_config is None:
            raise ValueError("Training configuration is required.")
        train = select_split(examples, "train")
        selected_data = train + validation
        soft, history = train_soft_prompt(
            engine,
            train,
            validation,
            template,
            train_config,
            progress=lambda row: print(json.dumps(row), file=sys.stderr, flush=True),
        )
        kind = "soft"
    selection = provenance(selected_data)
    if artifact is not None:
        # Preserve provenance of a text template even when it was selected using
        # a different validation file; later test overlap checks include both.
        previous = artifact["selection_data"]
        for field in ("ids", "prompt_sha256"):
            selection[field] = sorted(set(selection[field]) | set(previous[field]))
    metadata = {
        "kind": kind,
        "engine": engine.identity(),
        "environment": environment,
        "template": asdict(template),
        "selection_data": selection,
        "source_template": artifact,
        "history": history,
        "training": asdict(train_config) if train_config is not None else None,
        "objective": "macro-average target continuation negative log-likelihood",
    }
    save_artifact(args.output, metadata, soft)
    print(f"Saved {kind} prompt artifact to {args.output}")


def main(argv: list[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        cli.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
