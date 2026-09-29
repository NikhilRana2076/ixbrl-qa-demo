"""Tests for the evaluation harness (eval/). No API calls.

Scoring is tested as pure functions. The full pipeline is exercised on a
4-question Vodafone mini benchmark with a scripted "model" that reads the
evidence block the app builds and cites facts the way a real model would.
"""
import json
import re
import shutil
from types import SimpleNamespace

import pytest
from conftest import FIXTURES, SAMPLES

from eval import run_eval
from eval.scoring import gold_values, score, summarise

EXTRACTION = {"question_type": "extraction", "gold_answer": "2513000000", "acceptable_values": ""}


def fact_answer(value):
    return {"kind": "fact", "fact": {"raw_value": value}}


# ----------------------------------------------------------------------------- scoring
def test_fact_exact_match():
    assert score(EXTRACTION, fact_answer(2_513_000_000.0))["outcome"] == "correct"


def test_acceptable_alternative_counts():
    row = {**EXTRACTION, "gold_answer": "73712000000", "acceptable_values": "72886000000"}
    assert gold_values(row) == [73_712_000_000, 72_886_000_000]
    assert score(row, fact_answer(72_886_000_000.0))["outcome"] == "correct"


def test_prior_year_value_is_wrong():
    assert score(EXTRACTION, fact_answer(2_332_000_000.0))["outcome"] == "wrong"


def test_sign_mismatch_is_wrong_but_flagged():
    s = score({**EXTRACTION, "gold_answer": "-178000000"}, fact_answer(178_000_000.0))
    assert s["outcome"] == "wrong" and "sign differs" in s["detail"]


def test_computation_recomputed_from_raw_inputs():
    ans = {"kind": "computation", "operation": "percent_of",
           "inputs": [{"raw_value": 2_330_000_000}, {"raw_value": 2_513_000_000}]}
    row = {"question_type": "computation", "gold_answer": "92.72", "acceptable_values": ""}
    assert score(row, ans)["outcome"] == "correct"          # 92.718... within 0.5%


def test_abstain_unverified_and_error():
    assert score(EXTRACTION, {"kind": "not_found"})["outcome"] == "abstained"
    assert score(EXTRACTION, {"kind": "unverified", "status": {"detail": "x"}})["outcome"] == "unverified"
    assert score(EXTRACTION, None, "Timeout")["outcome"] == "error"


def test_disclosure_needs_right_concept_and_verified_quote():
    row = {"question_type": "disclosure", "concept": "ifrs-full:DisclosureOfRevenueExplanatory"}
    good = {"kind": "narrative", "source": {"concept": row["concept"]}, "status": {"level": "quoted"}}
    assert score(row, good)["outcome"] == "correct"
    assert score(row, {**good, "status": {"level": "unverified"}})["outcome"] == "wrong"
    assert score(row, {**good, "source": {"concept": "ifrs-full:Other"}})["outcome"] == "wrong"


def test_summarise_counts():
    rs = [{"question_type": "extraction", "outcome": o} for o in ("correct", "correct", "wrong", "abstained")]
    s = summarise(rs)["overall"]
    assert (s["n"], s["correct"], s["accuracy"]) == (4, 2, 0.5)
    assert s["error_rate_when_answered"] == pytest.approx(1 / 3, abs=1e-4)


def test_ci_subset_ids_exist_in_benchmark():
    ids = {r["question_id"] for r in run_eval.load_benchmark()}
    assert len(ids) == 102
    assert set(run_eval.CI_SUBSET) <= ids and len(run_eval.CI_SUBSET) == 20


# ----------------------------------------------------------------------------- pipeline
FACT_LINE = re.compile(r"\[F(\d+)\] (\S+) \| value = \S+ \S* ?\| period: ([^|]+)\| dimensions: (\S+)")


class OracleModel:
    """Answers like a well-behaved model: picks the undimensioned fact for the asked period."""

    def __init__(self, plan, pick_prior_year=False):
        self.plan, self.pick_prior_year = plan, pick_prior_year
        self.current = None
        self.spent_usd = 0.0

    def generate(self, prompt, temperature=None, max_tokens=0, purpose=""):
        self.spent_usd += 0.001
        if "RETRIEVED EVIDENCE" not in prompt:                     # search-term extraction call
            self.current = next(p for q, p in self.plan.items() if q in prompt)
            return {"text": ", ".join(self.current["terms"]), "estimated_cost_usd": 0.001}
        facts = [m.groups() for m in FACT_LINE.finditer(prompt)]
        p = self.current

        def pick(concept, year):
            # match on the period END ("... to 2026-03-31" or "as at 2026-03-31")
            return next(fid for fid, c, period, dims in facts
                        if c == concept and period.strip().split()[-1].startswith(year) and dims == "none")

        if p["type"] == "fact":
            year = "2025" if self.pick_prior_year else p["year"]
            reply = {"answer_type": "fact", "fact_ids": [f"F{pick(p['concept'], year)}"], "summary": "."}
        elif p["type"] == "computation":
            reply = {"answer_type": "computation", "operation": "difference", "summary": ".",
                     "fact_ids": [f"F{pick(p['concept'], '2026')}", f"F{pick(p['concept'], '2025')}"]}
        else:
            nid, body = re.search(r"\[N(\d+)\] " + re.escape(p["concept"]) + r":\s*\n(.+)", prompt).groups()
            quote = body.strip(" .…")[:60].strip()
            reply = {"answer_type": "narrative", "narrative_id": f"N{nid}", "quote": quote, "summary": "."}
        return {"text": json.dumps(reply), "estimated_cost_usd": 0.001}


PLAN = {
    "revenue for the year ended 31 March 2026": {"terms": ["Revenue"], "type": "fact",
                                                 "concept": "ifrs-full:Revenue", "year": "2026"},
    "cash and cash equivalents": {"terms": ["CashAndCashEquivalents"], "type": "fact",
                                  "concept": "ifrs-full:CashAndCashEquivalents", "year": "2026"},
    "increase between FY2025 and FY2026": {"terms": ["Revenue"], "type": "computation",
                                           "concept": "ifrs-full:Revenue"},
    "describe its revenue": {"terms": ["DisclosureOfRevenueExplanatory"], "type": "narrative",
                             "concept": "ifrs-full:DisclosureOfRevenueExplanatory"},
}


@pytest.fixture
def mini(tmp_path, monkeypatch):
    filings = tmp_path / "filings"
    filings.mkdir()
    shutil.copy(SAMPLES / "Vodafone.html", filings / "VOD_Vodafone.html")
    monkeypatch.setattr(run_eval, "FILINGS", filings)
    monkeypatch.setattr(run_eval, "STORES", tmp_path / "stores")
    orig = run_eval.build_store
    monkeypatch.setattr(run_eval, "build_store",
                        lambda c, rebuild=False: orig(c, filings, tmp_path / "stores", rebuild))
    return run_eval.load_benchmark(FIXTURES / "mini_benchmark.csv")


def args(**kw):
    base = dict(k=15, rebuild=False, tier="public", max_usd=1.0)
    return SimpleNamespace(**{**base, **kw})


def test_retrieval_mode_finds_every_gold_fact(mini):
    report = run_eval.run_retrieval(mini, args())
    assert report["summary"]["recall_at_15"] == 1.0
    assert report["summary"]["gold_not_in_store"] == []
    by_id = {r["question_id"]: r for r in report["results"]}
    assert by_id["V002"]["ranks"] == [1]            # current cash balance ranks first


def test_answer_mode_end_to_end(mini):
    report = run_eval.run_answers(mini, args(), client=OracleModel(PLAN))
    outcomes = {r["question_id"]: r["outcome"] for r in report["results"]}
    assert outcomes == {"V001": "correct", "V002": "correct", "V003": "correct", "V004": "correct"}
    assert report["summary"]["overall"]["accuracy"] == 1.0


def test_answer_mode_catches_wrong_period(mini):
    report = run_eval.run_answers(mini[:1], args(), client=OracleModel(PLAN, pick_prior_year=True))
    assert report["results"][0]["outcome"] == "wrong"


def test_budget_stops_the_run(mini):
    report = run_eval.run_answers(mini, args(max_usd=0.002), client=OracleModel(PLAN))
    assert report["stopped_early"] and len(report["results"]) < len(mini)
