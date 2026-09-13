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


# Prompt-level interventions to try, cheapest and least intrusive first, when the
# baseline response is judged a refusal. Each name must match a trained artifact
# already loaded on the runtime; missing ones are skipped, never trained on demand.
ESCALATION_ORDER = ["soft", "mse", "gcg", "pair", "autodan"]


def escalation_for(runtime, requested=None) -> list[str]:
    """Resolve an ordered, de-duplicated list of available prompt methods.

    ``requested`` overrides the default order; either way only methods that the
    runtime has already loaded as artifacts survive, and ``baseline`` is dropped
    because it is the response being escalated from."""
    order = requested if requested is not None else ESCALATION_ORDER
    available = getattr(runtime, "methods", {})
    if not isinstance(available, dict):
        return []
    seen: dict[str, None] = {}
    for method in order:
        if method != "baseline" and method in available and method not in seen:
            seen[method] = None
    return list(seen)


class AuditedChat:
    """A baseline response, then, only if it is judged a refusal, an ordered
    escalation over pre-trained prompt interventions until one is not refused.

    No optimizer runs at request time: escalation only *applies* artifacts that
    were trained offline. Weight-level decensoring is a separate, registered
    model, not something produced here."""

    def __init__(self, runtime, root: Path, judge=None):
        self.runtime = runtime
        self.judge = judge or ResponseJudge()
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "assessment_history.json"
        self.history = json.loads(self.path.read_text()) if self.path.exists() else []

    def _attempt(self, user_input: str, method: str, attempt: int, max_new_tokens: int):
        started = time.monotonic()
        generated = self.runtime.complete(
            [{"role": "user", "content": user_input}], method, max_new_tokens=max_new_tokens,
            return_metadata=True,
        )
        seconds = time.monotonic() - started
        judged = self.judge.assess(user_input, generated["response"])
        if generated["finish_reason"] != "eos":
            judged = {**assessment(user_input, generated["response"], judged["judge_output"], complete=False),
                      "error": "response_truncated"}
        row = {
            "attempt": attempt, "method": method, "elapsed_seconds": round(seconds, 2),
            "refused": judged["refused"], "status": judged["status"],
            "success": judged["success"], "success_verified": False,
        }
        return generated, judged, row

    def chat(self, user_input: str, max_new_tokens=512, escalation=None) -> dict:
        methods = escalation_for(self.runtime, escalation)
        generated, judged, row = self._attempt(user_input, "baseline", 1, max_new_tokens)
        log = [row]
        chosen_method, chosen_generated, chosen_judged = "baseline", generated, judged
        # Only a confident refusal warrants escalation; unknown/error stays baseline.
        if judged["refused"] is True:
            for index, method in enumerate(methods, start=2):
                generated, judged, row = self._attempt(user_input, method, index, max_new_tokens)
                log.append(row)
                if judged["refused"] is not True:
                    chosen_method, chosen_generated, chosen_judged = method, generated, judged
                    break
            else:
                # Every escalation still refused; report the last attempt.
                chosen_method, chosen_generated, chosen_judged = (
                    (methods[-1], generated, judged) if methods else ("baseline", generated, judged)
                )
        result = {
            **chosen_generated, "method": chosen_method, "attempts": len(log),
            "refused": chosen_judged["refused"], "baseline_refused": log[0]["refused"],
            "success": chosen_judged["success"], "success_verified": False,
            "assessment": chosen_judged,
            "optimization_performed": chosen_method != "baseline",
            "optimization_log": log,
        }
        # Old success-only logs contain no response and cannot be re-judged.
        # Keep them on disk, outside these verified assessment statistics.
        self.history.append({"timestamp": time.time(), "method": chosen_method, "assessment": chosen_judged})
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
