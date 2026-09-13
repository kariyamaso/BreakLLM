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


def _attempt(runtime, judge, user_input: str, method: str, attempt: int, max_new_tokens: int):
    started = time.monotonic()
    generated = runtime.complete(
        [{"role": "user", "content": user_input}], method, max_new_tokens=max_new_tokens,
        return_metadata=True,
    )
    seconds = time.monotonic() - started
    judged = judge.assess(user_input, generated["response"])
    if generated["finish_reason"] != "eos":
        # A cut-off answer can still be a clear refusal (refusals are front-loaded),
        # so keep the judge's refusal verdict — otherwise a truncated refusal reads
        # as "unknown" and never triggers escalation. Only the success claim, which
        # a truncated answer cannot establish, is withheld.
        judged = {**judged, "success": None, "success_verified": False, "error": "response_truncated"}
    row = {
        "attempt": attempt, "method": method, "elapsed_seconds": round(seconds, 2),
        "refused": judged["refused"], "status": judged["status"],
        "success": judged["success"], "success_verified": False,
    }
    return generated, judged, row


def run_escalation(runtime, judge, user_input: str, max_new_tokens=512, escalation=None) -> dict:
    """Answer at baseline, judge it, and only if it is a confident refusal walk the
    escalation methods (already loaded on ``runtime``) until one is not refused.

    Pure function over ``runtime`` and ``judge`` so it works on any selected model;
    it does not persist history. No optimizer runs here — escalation only applies
    artifacts trained offline."""
    methods = escalation_for(runtime, escalation)
    started = time.monotonic()
    generated, judged, row = _attempt(runtime, judge, user_input, "baseline", 1, max_new_tokens)
    log = [row]
    chosen_method, chosen_generated, chosen_judged = "baseline", generated, judged
    # Only a confident refusal warrants escalation; unknown/error stays baseline.
    if judged["refused"] is True:
        for index, method in enumerate(methods, start=2):
            generated, judged, row = _attempt(runtime, judge, user_input, method, index, max_new_tokens)
            log.append(row)
            if judged["refused"] is not True:
                chosen_method, chosen_generated, chosen_judged = method, generated, judged
                break
        else:
            # Every escalation still refused; report the last attempt.
            chosen_method, chosen_generated, chosen_judged = (
                (methods[-1], generated, judged) if methods else ("baseline", generated, judged)
            )
    return {
        **chosen_generated, "method": chosen_method, "attempts": len(log),
        "refused": chosen_judged["refused"], "baseline_refused": log[0]["refused"],
        "success": chosen_judged["success"], "success_verified": False,
        "assessment": chosen_judged,
        "optimization_performed": chosen_method != "baseline",
        "optimization_log": log,
        "seconds": round(time.monotonic() - started, 2), "tools": [],
    }


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

    def chat(self, user_input: str, max_new_tokens=512, escalation=None) -> dict:
        result = run_escalation(self.runtime, self.judge, user_input, max_new_tokens, escalation)
        # Old success-only logs contain no response and cannot be re-judged.
        # Keep them on disk, outside these verified assessment statistics.
        self.history.append({"timestamp": time.time(), "method": result["method"], "assessment": result["assessment"]})
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
