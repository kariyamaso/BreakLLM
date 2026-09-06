# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validated experiment inputs and portable text prompt templates."""

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Example:
    id: str
    prompt: str
    target: str
    split: str


@dataclass(frozen=True)
class Template:
    name: str = "identity"
    prefix: str = ""
    suffix: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Template name must be a nonempty string.")
        if not isinstance(self.prefix, str) or not isinstance(self.suffix, str):
            raise TypeError("Template prefix and suffix must be strings.")

    def render(self, prompt: str) -> str:
        # Concatenation deliberately leaves braces in user inputs untouched.
        return self.prefix + prompt + self.suffix


def prompt_key(prompt: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", prompt).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def read_examples(path: Path) -> list[Example]:
    examples = []
    ids: set[str] = set()
    prompts: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != {
                "id",
                "prompt",
                "target",
                "split",
            }:
                raise ValueError("Expected exactly id, prompt, target, split.")
            if any(
                not isinstance(value, str) or not value.strip()
                for value in row.values()
            ):
                raise ValueError("All fields must be nonempty strings.")
            example = Example(**row)
            if example.split not in {"train", "validation", "test"}:
                raise ValueError("split must be train, validation, or test.")
            key = prompt_key(example.prompt)
            if example.id in ids or key in prompts:
                raise ValueError(
                    "Duplicate id or normalized prompt; splits must be disjoint."
                )
            ids.add(example.id)
            prompts.add(key)
            examples.append(example)
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    if not examples:
        raise ValueError(f"{path}: no examples.")
    return examples


def select_split(examples: list[Example], split: str) -> list[Example]:
    selected = [example for example in examples if example.split == split]
    if not selected:
        raise ValueError(f"No examples in split {split!r}.")
    return selected


def read_templates(path: Path) -> list[Template]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Templates must be a nonempty JSON array.")
    templates = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"name", "prefix", "suffix"}:
            raise ValueError("Template fields must be name, prefix, suffix.")
        templates.append(Template(**row))
    if len({template.name for template in templates}) != len(templates):
        raise ValueError("Template names must be unique.")
    return templates


def dataset_hash(examples: list[Example]) -> str:
    payload = json.dumps(
        [asdict(e) for e in examples], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
