# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pinned, unmodified benchmark questions for fixed baseline safety evaluation."""

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

import httpx

PURPOSE = "fixed_baseline_safety_evaluation"
SOURCES: dict[str, dict] = {
    "advbench": {
        "revision": "098262edf85f807224e70ecd87b9d83716bf6b73",
        "base_url": "https://raw.githubusercontent.com/llm-attacks/llm-attacks",
        "filename": "data/advbench/harmful_behaviors.csv",
        "sha256": "6cd1a5c63c07610d7eb67307772ee5606017ee950b5770ab288a2c487489d3e1",
        "license": "MIT (repository)",
        "source_count": 520,
    },
    "harmbench": {
        "revision": "8e1604d1171fe8a48d8febecd22f600e462bdcdd",
        "base_url": "https://raw.githubusercontent.com/centerforaisafety/HarmBench",
        "filename": "data/behavior_datasets/harmbench_behaviors_text_all.csv",
        "sha256": "8d81accedd38eaaf8b760618622bb888417d1fd0c86eba65c427a16f1cbb4afc",
        "license": "MIT (repository)",
        "source_count": 400,
    },
    "do_not_answer_ja": {
        "revision": "8885330ded1853396458e8565c28ac62d7110281",
        "base_url": "https://huggingface.co/datasets/kunishou/do-not-answer-ja/resolve",
        "filename": "dna_ja_1k.json",
        "sha256": "7b6c778d7e36add8ddceb63ad0592afb6a38b3a0e1a92a4eaba0b735f3fbb5f0",
        "license": "CC-BY-NC-SA-4.0",
        "source_count": 939,
    },
}
EXCLUSION_REASONS = {
    "copyright": "Requires the official copyright overlap classifier; excluded here.",
    "multimodal": "Requires image inputs; excluded from text-only evaluation.",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or empty string field: {key}")
    return value


def parse_source(name: str, raw: bytes) -> tuple[list[dict], dict]:
    """Parse canonical schemas; retain duplicate questions and exclude unsupported types."""
    if name not in SOURCES:
        raise ValueError(f"Unknown dataset: {name}")
    decoded = raw.decode("utf-8-sig")
    records = (
        json.loads(decoded)
        if name == "do_not_answer_ja"
        else list(csv.DictReader(io.StringIO(decoded)))
    )
    if not isinstance(records, list) or not records:
        raise ValueError("Expected a nonempty list of dataset records.")
    rows, excluded, seen = [], Counter(), set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise TypeError("Dataset records must be objects.")
        extra = {}
        if name == "advbench":
            source_id, prompt, category = (
                f"{index:04d}",
                _text(record, "goal"),
                "unspecified",
            )
        elif name == "harmbench":
            source_id = _text(record, "BehaviorID")
            functional = _text(record, "FunctionalCategory")
            if source_id in seen:
                raise ValueError(f"Duplicate source ID in {name}: {source_id}")
            seen.add(source_id)
            if functional not in {"standard", "contextual"}:
                excluded[functional] += 1
                continue
            prompt, category = (
                _text(record, "Behavior"),
                _text(record, "SemanticCategory"),
            )
            extra["functional_category"] = functional
            if functional == "contextual":
                context = _text(record, "ContextString")
                prompt = context + "\n\n" + prompt
                extra["context_sha256"] = digest(context)
        else:
            source_id = _text(record, "id")
            prompt, category = _text(record, "question"), _text(record, "types_of_harm")
            extra = {key: _text(record, key) for key in ("risk_area", "specific_harms")}
        rows.append(
            {
                "id": f"{name}:{source_id}",
                "dataset": name,
                "source_id": source_id,
                "prompt": prompt,
                "prompt_sha256": digest(prompt),
                "language": "ja" if name == "do_not_answer_ja" else "en",
                "category": category,
                **extra,
            }
        )
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate source IDs in {name}.")
    return rows, {
        "source_count": len(records),
        "included_count": len(rows),
        "excluded_counts": dict(excluded),
        "exclusion_reasons": {
            key: EXCLUSION_REASONS.get(key, "Unsupported functional category.")
            for key in excluded
        },
    }


def validate_dataset(dataset: dict) -> None:
    """Check structure and internal consistency; verify_sources authenticates content."""
    if dataset.get("schema_version") != 1 or dataset.get("purpose") != PURPOSE:
        raise ValueError("Unsupported safety dataset schema or purpose.")
    rows, sources = dataset.get("rows"), dataset.get("sources")
    if not isinstance(rows, list) or not rows or not isinstance(sources, list):
        raise ValueError("A safety dataset requires rows and sources.")
    seen, counts = set(), Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("Dataset rows must be objects.")
        name = _text(row, "dataset")
        if name not in SOURCES:
            raise ValueError(f"Unknown row dataset: {name}")
        source_id, identity = _text(row, "source_id"), _text(row, "id")
        if identity != f"{name}:{source_id}" or identity in seen:
            raise ValueError("Invalid or duplicate row ID.")
        seen.add(identity)
        if row.get("prompt_sha256") != digest(_text(row, "prompt")):
            raise ValueError("Prompt content hash mismatch.")
        if row.get("language") != ("ja" if name == "do_not_answer_ja" else "en"):
            raise ValueError("Row language disagrees with the source.")
        _text(row, "category")
        counts[name] += 1
    names = set()
    for source in sources:
        if not isinstance(source, dict):
            raise TypeError("Source metadata must be objects.")
        name = _text(source, "name")
        if name in names or name not in SOURCES:
            raise ValueError("Duplicate or unknown source metadata.")
        names.add(name)
        spec = SOURCES[name]
        expected_url = f"{spec['base_url']}/{spec['revision']}/{spec['filename']}"
        if (
            source.get("revision") != spec["revision"]
            or source.get("sha256") != spec["sha256"]
            or source.get("url") != expected_url
        ):
            raise ValueError("Source provenance disagrees with the pinned manifest.")
        _text(source, "license")
        excluded = source.get("excluded_counts", {})
        numbers = [source.get("source_count"), source.get("included_count")]
        if not isinstance(excluded, dict):
            raise TypeError("Invalid source exclusion counts.")
        numbers.extend(excluded.values())
        if any(type(value) is not int or value < 0 for value in numbers):
            raise ValueError("Source counts must be nonnegative integers.")
        if counts[name] != source["included_count"] or source["source_count"] != source[
            "included_count"
        ] + sum(excluded.values()):
            raise ValueError("Source counts disagree with dataset rows.")
    if set(counts) - names:
        raise ValueError("Missing source metadata.")


def verify_sources(dataset: dict, dataset_path: Path) -> None:
    """Authenticate the complete corpus against pinned local source files, offline."""
    validate_dataset(dataset)
    sources = {source["name"]: source for source in dataset["sources"]}
    if set(sources) != set(SOURCES):
        raise ValueError("Dataset must include every pinned source.")
    source_dir = Path(dataset_path).parent / "sources"
    canonical_rows = {}
    for name, spec in SOURCES.items():
        # Resolve from the dataset location, never from untrusted local_file metadata.
        path = source_dir / f"{name}-{spec['revision']}-{Path(spec['filename']).name}"
        if not path.is_file():
            raise ValueError(f"Missing canonical source cache: {path}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError(f"Pinned source hash mismatch: {name}")
        rows, stats = parse_source(name, raw)
        if stats["source_count"] != spec["source_count"]:
            raise ValueError(f"Unexpected canonical source count: {name}")
        if any(sources[name].get(key) != value for key, value in stats.items()):
            raise ValueError(f"Canonical source statistics disagree: {name}")
        if sources[name]["license"] != spec["license"]:
            raise ValueError(f"Canonical source license disagrees: {name}")
        canonical_rows.update({row["id"]: row for row in rows})
    actual_rows = {row["id"]: row for row in dataset["rows"]}
    if actual_rows.keys() != canonical_rows.keys():
        raise ValueError("Dataset IDs differ from the complete canonical corpus.")
    if actual_rows != canonical_rows:
        raise ValueError("Dataset rows differ from the canonical source content.")


def prepare_dataset(output: Path) -> dict:
    """Download hash-pinned official sources and save a baseline-only evaluation set."""
    output = Path(output)
    source_dir = output.parent / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    rows, sources = [], []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for name, spec in SOURCES.items():
            url = f"{spec['base_url']}/{spec['revision']}/{spec['filename']}"
            path = (
                source_dir / f"{name}-{spec['revision']}-{Path(spec['filename']).name}"
            )
            raw = (
                path.read_bytes()
                if path.exists()
                else client.get(url).raise_for_status().content
            )
            if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
                raise ValueError(f"Pinned source hash mismatch: {name}")
            parsed, stats = parse_source(name, raw)
            if stats["source_count"] != spec["source_count"]:
                raise ValueError(f"Unexpected source count: {name}")
            path.write_bytes(raw)
            rows.extend(parsed)
            sources.append(
                {
                    "name": name,
                    "url": url,
                    "revision": spec["revision"],
                    "sha256": spec["sha256"],
                    "license": spec["license"],
                    "local_file": str(path),
                    **stats,
                }
            )
    groups = defaultdict(list)
    for row in rows:
        groups[row["prompt_sha256"]].append(row["id"])
    dataset = {
        "schema_version": 1,
        "purpose": PURPOSE,
        "sources": sources,
        "rows": rows,
        "duplicate_prompt_groups": [ids for ids in groups.values() if len(ids) > 1],
        "notes": [
            "English sources remain English; no automatic translation is performed.",
            "Do-Not-Answer-Ja is translated and culturally adapted, not Japanese-origin data.",
            "Do-Not-Answer-Ja's train split is used for evaluation, not model training.",
            "HarmBench text_all has no multimodal rows; 110 multimodal behaviors are out of scope.",
            "HarmBench contextual prompts preserve the context followed by the behavior.",
            "Duplicate prompts are reported and retained as canonical benchmark entries.",
            "No response targets, attack templates, optimization or training are included.",
        ],
    }
    validate_dataset(dataset)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return dataset
