# SPDX-License-Identifier: AGPL-3.0-or-later
"""Supervised soft prompts and text-template selection, independent of abliteration."""

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from .data import Example, Template, dataset_hash, prompt_key, write_json


class TemplateScore(TypedDict):
    template: dict[str, str]
    validation_target_nll: float


class EvaluationRow(TypedDict):
    id: str
    variant: str
    prompt: str
    rendered_prompt: str
    target: str
    response: str
    target_nll: float
    exact_match: bool
    input_guardrail_pass: None
    output_guardrail_pass: None
    behavior_success: None


@dataclass(frozen=True)
class TrainConfig:
    tokens: int = 16
    epochs: int = 10
    learning_rate: float = 0.01
    batch_size: int = 4
    seed: int = 0
    init_text: str = "Please answer the question clearly."

    def __post_init__(self):
        if min(self.tokens, self.epochs, self.batch_size) < 1:
            raise ValueError("tokens, epochs, and batch_size must be positive.")
        if not math.isfinite(self.learning_rate) or self.learning_rate <= 0:
            raise ValueError("learning_rate must be finite and positive.")
        if not self.init_text.strip():
            raise ValueError("init_text must not be empty.")


class PromptEngine:
    """Single-device, text-only causal LM with an optional continuous prefix.

    The prefix precedes the entire serialized chat, as in input prompt tuning.
    It is never inserted into the vocabulary or projected to text implicitly.
    """

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        *,
        model_id: str,
        revision: str | None = None,
        system: str = "",
        max_length: int = 2048,
        plain: bool = False,
    ):
        if max_length < 2:
            raise ValueError("max_length must be at least 2.")
        if getattr(model.config, "is_encoder_decoder", False):
            raise ValueError("Only decoder-only causal language models are supported.")
        devices = {parameter.device for parameter in model.parameters()}
        if len(devices) != 1 or next(iter(devices)).type == "meta":
            raise ValueError("Prompt Lab requires a model loaded on one device.")
        self.model = model.eval()
        self.model.requires_grad_(False)
        self.model.zero_grad(set_to_none=True)
        self.tokenizer = tokenizer
        self.model_id = model_id
        self.revision = revision
        self.system = system
        self.plain = plain
        if not plain and not tokenizer.chat_template:
            raise ValueError(
                "Tokenizer has no chat template; pass --plain for a base LM."
            )
        limits = [max_length]
        for limit in (
            getattr(model.config, "max_position_embeddings", None),
            getattr(tokenizer, "model_max_length", None),
        ):
            if isinstance(limit, int) and 1 < limit < 1_000_000_000:
                limits.append(limit)
        self.max_length = min(limits)
        self.device = model.get_input_embeddings().weight.device
        self.dtype = model.get_input_embeddings().weight.dtype

    def identity(self) -> dict:
        tokenizer_payload = {
            "vocab": self.tokenizer.get_vocab(),
            "special_tokens": self.tokenizer.special_tokens_map,
            "chat_template": self.tokenizer.chat_template,
            "backend": self.tokenizer.backend_tokenizer.to_str()
            if getattr(self.tokenizer, "is_fast", False)
            else type(self.tokenizer).__name__,
        }
        fingerprint = hashlib.sha256(
            json.dumps(tokenizer_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return {
            "model_id": self.model_id,
            "revision": self.revision,
            "resolved_commit": getattr(self.model.config, "_commit_hash", None),
            "model_type": self.model.config.model_type,
            "embedding_shape": list(self.model.get_input_embeddings().weight.shape),
            "tokenizer_sha256": fingerprint,
            "system": self.system,
            "plain": self.plain,
            "max_length": self.max_length,
        }

    def encode_prompt(self, prompt: str, template: Template) -> list[int]:
        content = template.render(prompt)
        if self.plain:
            text = (self.system + "\n\n" if self.system else "") + content
            ids = self.tokenizer.encode(text, add_special_tokens=True)
        else:
            messages = []
            if self.system:
                messages.append({"role": "system", "content": self.system})
            messages.append({"role": "user", "content": content})
            ids = self.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=False
            )
        if not ids:
            raise ValueError("Prompt encoded to zero tokens.")
        return ids

    def target_ids(self, target: str) -> list[int]:
        ids = self.tokenizer.encode(target, add_special_tokens=False)
        if not ids:
            raise ValueError("Target encoded to zero tokens.")
        return ids

    def _check_length(self, length: int) -> None:
        if length > self.max_length:
            raise ValueError(
                f"Sequence needs {length} tokens, exceeding limit {self.max_length}; "
                "inputs and targets are never silently truncated."
            )

    def _embeddings(self, ids: list[int], soft: Tensor | None) -> Tensor:
        inputs = self.model.get_input_embeddings()(
            torch.tensor(ids, device=self.device, dtype=torch.long)
        )
        if soft is not None:
            inputs = torch.cat([soft.to(device=self.device, dtype=self.dtype), inputs])
        return inputs.unsqueeze(0)

    def loss(
        self, example: Example, template: Template, soft: Tensor | None = None
    ) -> Tensor:
        prompt = self.encode_prompt(example.prompt, template)
        target = self.target_ids(example.target)
        prefix_length = 0 if soft is None else soft.shape[0]
        self._check_length(prefix_length + len(prompt) + len(target))
        inputs = self._embeddings(prompt + target, soft)
        outputs = self.model(
            inputs_embeds=inputs,
            attention_mask=torch.ones(
                inputs.shape[:2], dtype=torch.long, device=self.device
            ),
            use_cache=False,
        )
        start = prefix_length + len(prompt) - 1
        logits = outputs.logits[0, start : start + len(target)].float()
        # Only the desired continuation contributes; no prompt-token or padding loss.
        loss = F.cross_entropy(logits, torch.tensor(target, device=logits.device))
        if not torch.isfinite(loss):
            raise ValueError("Non-finite target loss; check model dtype and inputs.")
        return loss

    @torch.no_grad()
    def mean_loss(
        self, examples: list[Example], template: Template, soft: Tensor | None = None
    ) -> float:
        if not examples:
            raise ValueError("Cannot evaluate an empty dataset.")
        return sum(float(self.loss(e, template, soft)) for e in examples) / len(
            examples
        )

    @torch.no_grad()
    def generate(
        self,
        prompt: str,
        template: Template,
        soft: Tensor | None = None,
        max_new_tokens: int = 128,
    ) -> str:
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens must be positive.")
        ids = self.encode_prompt(prompt, template)
        prefix_length = 0 if soft is None else soft.shape[0]
        self._check_length(prefix_length + len(ids) + max_new_tokens)
        inputs = self._embeddings(ids, soft)
        # With inputs_embeds and no input_ids, decoder-only generate returns only
        # the continuation. Do not trim it by the length of the original prompt.
        generated = self.model.generate(
            inputs_embeds=inputs,
            attention_mask=torch.ones(
                inputs.shape[:2], dtype=torch.long, device=self.device
            ),
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.model.generation_config.eos_token_id,
            use_cache=True,
        )
        return self.tokenizer.decode(generated[0], skip_special_tokens=True)


def assert_disjoint(left: list[Example], right: list[Example]) -> None:
    if {e.id for e in left} & {e.id for e in right} or {
        prompt_key(e.prompt) for e in left
    } & {prompt_key(e.prompt) for e in right}:
        raise ValueError("Optimization and evaluation data overlap.")


def provenance(examples: list[Example]) -> dict:
    return {
        "dataset_sha256": dataset_hash(examples),
        "ids": [e.id for e in examples],
        "prompt_sha256": [prompt_key(e.prompt) for e in examples],
    }


def assert_unseen(examples: list[Example], artifact: dict) -> None:
    seen = artifact["selection_data"]
    if {e.id for e in examples} & set(seen["ids"]) or {
        prompt_key(e.prompt) for e in examples
    } & set(seen["prompt_sha256"]):
        raise ValueError("Test data overlaps with data used to optimize this artifact.")


def search_templates(
    engine: PromptEngine, validation: list[Example], templates: list[Template]
) -> tuple[Template, list[TemplateScore]]:
    if not validation or any(e.split != "validation" for e in validation):
        raise ValueError("Template selection requires the validation split.")
    if not templates or len({t.name for t in templates}) != len(templates):
        raise ValueError("Provide templates with unique names.")
    scores: list[TemplateScore] = [
        {
            "template": asdict(t),
            "validation_target_nll": engine.mean_loss(validation, t),
        }
        for t in templates
    ]
    scores.sort(key=lambda row: row["validation_target_nll"])
    return Template(**scores[0]["template"]), scores


def train_soft_prompt(
    engine: PromptEngine,
    train: list[Example],
    validation: list[Example],
    template: Template,
    config: TrainConfig,
    progress: Any = None,
) -> tuple[Tensor, list[dict]]:
    if not train or any(e.split != "train" for e in train):
        raise ValueError("Training requires the train split.")
    if not validation or any(e.split != "validation" for e in validation):
        raise ValueError("Checkpoint selection requires the validation split.")
    assert_disjoint(train, validation)
    init_ids = engine.tokenizer.encode(config.init_text, add_special_tokens=False)
    if not init_ids:
        raise ValueError("Initialization text encoded to zero tokens.")
    init_ids = (init_ids * math.ceil(config.tokens / len(init_ids)))[: config.tokens]
    with torch.no_grad():
        initial = engine.model.get_input_embeddings()(
            torch.tensor(init_ids, device=engine.device)
        ).float()
    soft = nn.Parameter(initial.clone())
    optimizer = torch.optim.AdamW([soft], lr=config.learning_rate, weight_decay=0.0)
    rng = random.Random(config.seed)
    best = soft.detach().clone()
    best_loss = engine.mean_loss(validation, template, soft)
    history = [{"epoch": 0, "validation_target_nll": best_loss}]
    if progress:
        progress(history[-1])
    for epoch in range(1, config.epochs + 1):
        order = list(train)
        rng.shuffle(order)
        train_loss = 0.0
        with torch.enable_grad():
            for start in range(0, len(order), config.batch_size):
                batch = order[start : start + config.batch_size]
                optimizer.zero_grad(set_to_none=True)
                # Gradient accumulation avoids padding and keeps only one example's
                # activations in memory, even when prompt lengths differ.
                for example in batch:
                    loss = engine.loss(example, template, soft)
                    train_loss += float(loss.detach())
                    (loss / len(batch)).backward()
                if soft.grad is None or not torch.isfinite(soft.grad).all():
                    raise ValueError("Missing or non-finite soft-prompt gradients.")
                torch.nn.utils.clip_grad_norm_([soft], 1.0, error_if_nonfinite=True)
                optimizer.step()
        validation_loss = engine.mean_loss(validation, template, soft)
        row = {
            "epoch": epoch,
            "train_target_nll": train_loss / len(train),
            "validation_target_nll": validation_loss,
        }
        history.append(row)
        if progress:
            progress(row)
        if validation_loss < best_loss:
            best_loss = validation_loss
            best = soft.detach().clone()
    return best.cpu(), history


def save_artifact(path: Path, metadata: dict, soft: Tensor | None = None) -> None:
    # Existing experiment directories are not overwritten accidentally.
    path.mkdir(parents=True, exist_ok=False)
    payload = dict(metadata, format_version=1)
    if soft is not None:
        torch.save(
            {"soft_prompt": soft.detach().float().cpu()}, path / "soft_prompt.pt"
        )
        payload["weights_sha256"] = hashlib.sha256(
            (path / "soft_prompt.pt").read_bytes()
        ).hexdigest()
    write_json(path / "artifact.json", payload)


def load_artifact(
    path: Path, engine: PromptEngine | None = None
) -> tuple[dict, Tensor | None]:
    metadata = json.loads((path / "artifact.json").read_text(encoding="utf-8"))
    if metadata.get("format_version") != 1 or metadata.get("kind") not in {
        "soft",
        "text",
    }:
        raise ValueError("Unsupported prompt artifact format.")
    if engine is not None:
        # max_length is a serving-time sequence cap, not a training invariant: the
        # soft-prompt vectors and templates are valid regardless of it. Compare
        # every other identity field so raising the serving context length does not
        # spuriously invalidate artifacts trained at a smaller cap.
        saved = {k: v for k, v in metadata["engine"].items() if k != "max_length"}
        current = {k: v for k, v in engine.identity().items() if k != "max_length"}
        if saved != current:
            raise ValueError(
                "Artifact model/tokenizer/chat settings differ from the loaded model."
            )
    soft = None
    if metadata["kind"] == "soft":
        weights = path / "soft_prompt.pt"
        if (
            hashlib.sha256(weights.read_bytes()).hexdigest()
            != metadata["weights_sha256"]
        ):
            raise ValueError("Soft-prompt checksum mismatch.")
        soft = torch.load(weights, map_location="cpu", weights_only=True)["soft_prompt"]
        shape = (
            metadata["training"]["tokens"],
            metadata["engine"]["embedding_shape"][1],
        )
        if not isinstance(soft, Tensor) or tuple(soft.shape) != shape:
            raise ValueError("Soft-prompt tensor shape mismatch.")
        if not soft.is_floating_point() or not torch.isfinite(soft).all():
            raise ValueError("Invalid soft-prompt tensor.")
    return metadata, soft


def evaluate(
    engine: PromptEngine,
    examples: list[Example],
    template: Template,
    soft: Tensor | None,
    max_new_tokens: int,
) -> dict:
    if not examples or any(e.split != "test" for e in examples):
        raise ValueError("Final evaluation requires the test split.")
    variants: list[tuple[str, Template, Tensor | None]] = [
        ("baseline", Template(), None)
    ]
    if template.prefix or template.suffix:
        variants.append(("text", template, None))
    if soft is not None:
        variants.append(("soft", template, soft))
    rows: list[EvaluationRow] = []
    for name, candidate, weights in variants:
        for example in examples:
            with torch.no_grad():
                loss = float(engine.loss(example, candidate, weights))
            response = engine.generate(
                example.prompt, candidate, weights, max_new_tokens
            )
            rows.append(
                {
                    "id": example.id,
                    "variant": name,
                    "prompt": example.prompt,
                    "rendered_prompt": candidate.render(example.prompt),
                    "target": example.target,
                    "response": response,
                    "target_nll": loss,
                    "exact_match": response.strip() == example.target.strip(),
                    "input_guardrail_pass": None,
                    "output_guardrail_pass": None,
                    "behavior_success": None,
                }
            )
    summary = {}
    for name, _, _ in variants:
        selected = [row for row in rows if row["variant"] == name]
        summary[name] = {
            "count": len(selected),
            "mean_target_nll": sum(r["target_nll"] for r in selected) / len(selected),
            "exact_match_rate": sum(r["exact_match"] for r in selected) / len(selected),
        }
    return {"summary": summary, "examples": rows}
