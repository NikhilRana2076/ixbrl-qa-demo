"""Score one TagTrace answer against one benchmark row.

Pure functions, no I/O, so they are unit-tested directly.

Outcomes:
  correct     the verified value (or cited disclosure) matches the gold answer
  wrong       a verified answer that does not match
  abstained   the app said "Not in tagged data"
  unverified  the app could not verify the model's answer (shown with a warning badge)
  error       the pipeline raised

Numbers are compared on the verified value the server read or computed, never on
the model's free-text summary. Disclosures are scored automatically as correct when
the app cites the gold disclosure concept AND its quote is found verbatim. That is
a proxy for the human judgement used in the dissertation, so CI accuracy is a
regression signal for the live app, not a replication of the thesis figures.
"""
from __future__ import annotations

import math

REL_TOL = 0.005        # 0.5%: absorbs rounding in percentages and per-share figures
ABS_TOL = 1e-9

COMPUTE_OPS = {
    "difference": lambda a, b: a - b,
    "sum": lambda a, b: a + b,
    "ratio": lambda a, b: None if b == 0 else a / b,
    "percent_of": lambda a, b: None if b == 0 else a / b * 100,
    "percent_change": lambda a, b: None if b == 0 else (a - b) / abs(b) * 100,
}


def gold_values(row: dict) -> list[float]:
    """The gold answer plus any pipe-separated acceptable alternatives."""
    values = []
    for raw in [row.get("gold_answer")] + str(row.get("acceptable_values") or "").split("|"):
        try:
            if raw not in (None, ""):
                values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return values


def numbers_match(pred: float | None, gold: float) -> bool:
    if pred is None or (isinstance(pred, float) and math.isnan(pred)):
        return False
    return math.isclose(pred, gold, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def predicted_number(answer: dict) -> float | None:
    """The number the app actually displayed as verified, recomputed from raw inputs."""
    kind = answer.get("kind")
    if kind == "fact":
        v = answer.get("fact", {}).get("raw_value")
        return None if v is None else float(v)
    if kind == "computation":
        inputs = answer.get("inputs") or []
        op = COMPUTE_OPS.get(answer.get("operation", ""))
        if op is None or len(inputs) != 2:
            return None
        try:
            return op(float(inputs[0]["raw_value"]), float(inputs[1]["raw_value"]))
        except (KeyError, TypeError, ValueError):
            return None
    return None


def score(row: dict, answer: dict | None, error: str | None = None) -> dict:
    """Return {"outcome": ..., "predicted": ..., "detail": ...} for one question."""
    if error is not None or answer is None:
        return {"outcome": "error", "predicted": None, "detail": error or "no answer"}

    kind = answer.get("kind")
    if kind == "not_found":
        return {"outcome": "abstained", "predicted": None, "detail": "not in tagged data"}
    if kind == "unverified":
        return {"outcome": "unverified", "predicted": None,
                "detail": (answer.get("status") or {}).get("detail", "")}

    if row["question_type"] == "disclosure":
        if kind != "narrative":
            return {"outcome": "wrong", "predicted": kind, "detail": "expected a disclosure answer"}
        cited = (answer.get("source") or {}).get("concept")
        quoted = (answer.get("status") or {}).get("level") == "quoted"
        ok = quoted and cited == row.get("concept")
        return {"outcome": "correct" if ok else "wrong", "predicted": cited,
                "detail": "" if ok else ("quote not verified" if not quoted else f"cited {cited}")}

    pred = predicted_number(answer)
    golds = gold_values(row)
    if any(numbers_match(pred, g) for g in golds):
        return {"outcome": "correct", "predicted": pred, "detail": ""}
    detail = f"expected {golds[0] if golds else '?'}"
    if pred is not None and any(numbers_match(-pred, g) for g in golds):
        detail += " (sign differs)"
    return {"outcome": "wrong", "predicted": pred, "detail": detail}


def summarise(results: list[dict]) -> dict:
    """Aggregate outcomes overall and by question type."""
    def block(rs):
        n = len(rs)
        counts = {k: sum(r["outcome"] == k for r in rs)
                  for k in ("correct", "wrong", "abstained", "unverified", "error")}
        answered = counts["correct"] + counts["wrong"]
        return {"n": n, **counts,
                "accuracy": round(counts["correct"] / n, 4) if n else 0.0,
                "error_rate_when_answered": round(counts["wrong"] / answered, 4) if answered else 0.0}

    by_type = {}
    for r in results:
        by_type.setdefault(r["question_type"], []).append(r)
    return {"overall": block(results), "by_type": {k: block(v) for k, v in sorted(by_type.items())}}
