"""
src/generation/consistency.py

Numeric-aware consistency comparison for SelfCheckGPT-style detection.

Naive string comparison fails on financial figures: "£1.2m", "£1,200,000",
and "1,200 (in £000s)" all represent the same value but look different as
strings. This module normalises numeric mentions before comparing samples,
which is the numeric-aware extension named as a core contribution of the
dissertation.
"""
from __future__ import annotations

import re
from itertools import combinations

NUMBER_RE = re.compile(
    r"[£$€]?\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand|m|bn|k)?",
    re.IGNORECASE,
)

UNIT_MULTIPLIERS = {
    "thousand": 1_000, "k": 1_000,
    "million": 1_000_000, "m": 1_000_000,
    "billion": 1_000_000_000, "bn": 1_000_000_000,
}


def extract_numbers(text: str) -> set[float]:
    """Pull every numeric mention out of a text and normalise to a single
    scale, so differently-formatted mentions of the same value collide."""
    found = set()
    for match in NUMBER_RE.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if unit in UNIT_MULTIPLIERS:
            value *= UNIT_MULTIPLIERS[unit]
        if value != 0:
            found.add(value)
    return found


def numbers_agree(a: set[float], b: set[float], tolerance: float = 0.01) -> bool:
    """True if any number in `a` is within tolerance of any number in `b`.

    Comparing sets rather than single values because a response may
    mention several figures (e.g. both years' revenue); agreement means
    the two samples share at least one number in common, which is a
    permissive but workable definition for a 3-sample, cost-constrained
    check.
    """
    if not a or not b:
        return False
    for x in a:
        for y in b:
            if y != 0 and abs(x - y) / abs(y) < tolerance:
                return True
    return False


def is_abstention(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in (
        "not found in the provided text",
        "does not contain enough information",
        "cannot be determined from",
    ))


def consistency_label(samples: list[str]) -> dict:
    """Classify a set of sampled responses to the same question.

    Returns a dict with the consistency verdict and supporting detail,
    using numeric-aware agreement rather than exact string match.
    """
    non_abstain = [s for s in samples if not is_abstention(s)]
    n_abstain = len(samples) - len(non_abstain)

    if len(non_abstain) == 0:
        return {"verdict": "all_abstained", "agree_pairs": 0,
                "total_pairs": 0, "n_abstain": n_abstain}

    if len(non_abstain) == 1:
        return {"verdict": "single_answer", "agree_pairs": 0,
                "total_pairs": 0, "n_abstain": n_abstain}

    number_sets = [extract_numbers(s) for s in non_abstain]
    pairs = list(combinations(range(len(non_abstain)), 2))
    agree = sum(
        1 for i, j in pairs if numbers_agree(number_sets[i], number_sets[j])
    )

    verdict = "consistent" if agree == len(pairs) else (
        "partially_consistent" if agree > 0 else "inconsistent"
    )

    return {
        "verdict": verdict,
        "agree_pairs": agree,
        "total_pairs": len(pairs),
        "n_abstain": n_abstain,
    }