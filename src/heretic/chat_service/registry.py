# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lazy, memory-bounded registry of chat runtimes so the UI can switch models.

The serving GPU is shared with other services, so only ``max_loaded`` models
stay resident; switching to another model evicts the least recently used one.
"""
import gc
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any


def parse_model_list(value: str | None, default: str) -> list[str]:
    ids = [item.strip() for item in (value or "").split(",") if item.strip()]
    if default not in ids:
        ids.insert(0, default)
    return list(dict.fromkeys(ids))


def load_catalog(path: Path) -> dict[str, dict]:
    """Read a models.json catalog of imported models keyed by served id.

    Each entry is ``{"id", "label"?, "decensored"?}``; ``id`` is what the runtime
    loads (a Hugging Face id or a local path to weights, e.g. one produced by the
    weight-level decensoring script). Absent or malformed files yield an empty
    catalog so the service still starts on the default model alone."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    catalog: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        catalog[model_id] = {
            "label": entry.get("label") or model_label(model_id),
            "decensored": bool(entry.get("decensored", False)),
        }
    return catalog


def model_label(model_id: str) -> str:
    return model_id.rstrip("/").split("/")[-1]


class ModelRegistry:
    def __init__(
        self,
        model_ids: list[str],
        loader: Callable[[str], Any],
        *,
        max_loaded: int = 1,
        supports_images: bool = False,
        catalog: dict[str, dict] | None = None,
    ):
        if not model_ids:
            raise ValueError("At least one model id is required.")
        if max_loaded < 1:
            raise ValueError("max_loaded must be at least 1.")
        self.model_ids = list(model_ids)
        self.default_id = model_ids[0]
        self.loader = loader
        self.max_loaded = max_loaded
        self.supports_images = supports_images
        self.catalog = catalog or {}
        self.lock = threading.Lock()
        self.loaded: OrderedDict[str, Any] = OrderedDict()
        self.contexts: dict[str, dict] = {}
        self.loaded_at: dict[str, float] = {}

    def has(self, model_id: str) -> bool:
        return model_id in self.model_ids

    def resolve(self, model_id: str | None) -> str:
        if model_id is None:
            return self.default_id
        if not self.has(model_id):
            raise ValueError(f"Unknown model: {model_id}")
        return model_id

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self.loaded

    def get(self, model_id: str | None = None):
        """Return the runtime for ``model_id``, loading it (and evicting) as needed."""
        model_id = self.resolve(model_id)
        with self.lock:
            if model_id in self.loaded:
                self.loaded.move_to_end(model_id)
                return self.loaded[model_id]
            while len(self.loaded) >= self.max_loaded:
                evicted, runtime = self.loaded.popitem(last=False)
                self.loaded_at.pop(evicted, None)
                del runtime
                gc.collect()
                self._release_accelerator_memory()
            runtime = self.loader(model_id)
            self.loaded[model_id] = runtime
            self.loaded_at[model_id] = time.time()
            if model_id not in self.contexts:
                self.contexts[model_id] = self._audit_context(runtime)
            return runtime

    def audit_context(self, model_id: str | None = None) -> dict:
        model_id = self.resolve(model_id)
        if model_id not in self.contexts:
            self.get(model_id)
        return self.contexts[model_id]

    def describe(self) -> list[dict]:
        return [
            {
                "id": model_id,
                "label": self.catalog.get(model_id, {}).get("label", model_label(model_id)),
                "decensored": self.catalog.get(model_id, {}).get("decensored", False),
                "default": model_id == self.default_id,
                "loaded": model_id in self.loaded,
                "supports_images": self.supports_images,
                "methods": sorted(self.loaded[model_id].methods)
                if model_id in self.loaded
                else None,
            }
            for model_id in self.model_ids
        ]

    @staticmethod
    def _audit_context(runtime) -> dict:
        context = {
            "engine": runtime.engine.identity(),
            "methods": runtime.metadata,
            "generation": {"do_sample": False, "enable_tools": False},
        }
        identifier = hashlib.sha256(
            json.dumps(context, sort_keys=True).encode()
        ).hexdigest()
        return {"id": identifier, **context}

    @staticmethod
    def _release_accelerator_memory():
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def artifact_root_for(report_root: Path, model_id: str, default_id: str) -> Path:
    """Artifacts are trained per model; non-default models keep their own folder."""
    if model_id == default_id:
        return report_root / "methods"
    slug = model_id.replace("/", "__")
    return report_root / "models" / slug / "methods"
