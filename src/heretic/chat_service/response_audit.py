# SPDX-License-Identifier: AGPL-3.0-or-later
"""Response assessment, independent of prompt search and generation success."""

import hashlib
import json
import threading
import time
from pathlib import Path

from .safety_benchmark import GUARD, GUARD_REVISION, parse_judgment


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def assessment(prompt: str, response: str, judge_text: str, *, complete=True) -> dict:
    labels = parse_judgment(judge_text) if complete and response.strip() else {}
    refused = {"Yes": True, "No": False}.get(labels.get("refusal"))
    return {
        "schema_version": 1,
        "refused": refused,
        "status": "refusal" if refused is True else "non_refusal" if refused is False else "unknown",
        "safety": labels.get("safety"),
        # Non-refusal and the guard's Unsafe label do not establish task success.
        "success": False if refused is True else None,
        "success_verified": False,
        "judge": GUARD,
        "judge_revision": GUARD_REVISION,
        "judge_output": judge_text,
        "prompt_sha256": digest(prompt),
        "response_sha256": digest(response),
    }


class ResponseJudge:
    """Lazily load the separate, pinned safety classifier on CPU."""

    def __init__(self):
        self.model = None
        self.lock = threading.Lock()

    def assess(self, prompt: str, response: str) -> dict:
        if not response.strip():
            return assessment(prompt, response, "", complete=False)
        with self.lock:
            try:
                if self.model is None:
                    from .safety_benchmark import BatchModel

                    self.model = BatchModel(GUARD, GUARD_REVISION, "cpu")
                encoded = self.model.encode(
                    [{"role": "user", "content": prompt}, {"role": "assistant", "content": response}],
                    guard=True,
                )
                if len(encoded) + 128 > 8192:
                    return {**assessment(prompt, response, "", complete=False), "error": "judge_input_too_long"}
                output = self.model.generate([encoded], 128)[0]
                return assessment(prompt, response, output["response"], complete=output["finish_reason"] == "eos")
            except (OSError, ValueError, RuntimeError) as error:
                return {**assessment(prompt, response, "", complete=False), "error": type(error).__name__}


class AuditedChat:
    """One baseline response followed by assessment; no refusal-driven search."""

    def __init__(self, runtime, root: Path, judge=None):
        self.runtime = runtime
        self.judge = judge or ResponseJudge()
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "assessment_history.json"
        self.history = json.loads(self.path.read_text()) if self.path.exists() else []

    def chat(self, user_input: str, max_new_tokens=512) -> dict:
        started = time.monotonic()
        generated = self.runtime.complete(
            [{"role": "user", "content": user_input}], "baseline", max_new_tokens=max_new_tokens,
            return_metadata=True,
        )
        response = generated["response"]
        seconds = time.monotonic() - started
        judged = self.judge.assess(user_input, response)
        if generated["finish_reason"] != "eos":
            judged = {**assessment(user_input, response, judged["judge_output"], complete=False),
                      "error": "response_truncated"}
        row = {
            "attempt": 1, "method": "baseline", "elapsed_seconds": round(seconds, 2),
            "refused": judged["refused"], "status": judged["status"],
            "success": judged["success"], "success_verified": False,
        }
        result = {
            **generated, "method": "baseline", "attempts": 1,
            "refused": judged["refused"], "baseline_refused": judged["refused"],
            "success": judged["success"], "success_verified": False,
            "assessment": judged, "optimization_performed": False, "optimization_log": [row],
        }
        # Old success-only logs contain no response and cannot be re-judged.
        # Keep them on disk, outside these verified assessment statistics.
        self.history.append({"timestamp": time.time(), "method": "baseline", "assessment": judged})
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.history, ensure_ascii=False, indent=2))
        temporary.replace(self.path)
        return result

    def get_history(self):
        return self.history

    def get_stats(self):
        refusals = sum(row["assessment"]["refused"] is True for row in self.history)
        judged = sum(row["assessment"]["refused"] is not None for row in self.history)
        return {
            "total": len(self.history), "refusal_yes": refusals, "refusal_judged": judged,
            "refusal_rate": refusals / judged if judged else None,
            "unknown": len(self.history) - judged, "success": None, "success_rate": None,
            "success_verified": False,
        }
