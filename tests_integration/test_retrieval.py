"""Retrieval behaviour on a real filing (samples/Vodafone.html, FY to 31 March 2026).

These guard the web app's keyword retriever against *selection* errors: returning
the wrong period, a breakdown instead of the total, or no balance-sheet fact at all.
"""
import pytest
from conftest import SAMPLES


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    from src.ingestion.parser import parse_filing
    from src.ingestion.store import connect, insert_company, load_filing

    db = tmp_path_factory.mktemp("store") / "facts.sqlite"
    c = connect(db)
    insert_company(c, {"company_number": "VOD"})
    load_filing(c, "VOD", parse_filing(SAMPLES / "Vodafone.html"))
    c.commit()
    yield c
    c.close()


def search(conn, *keywords, limit=15):
    from src.generation.adhoc_retrieval import retrieve_by_keywords

    return retrieve_by_keywords(conn, list(keywords), limit=limit)


def test_store_loaded(conn):
    n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert n == 752


def test_revenue_current_year_total_comes_first(conn):
    top = search(conn, "Revenue")[0]
    assert top["local_name"] == "Revenue"
    assert top["period_end"] == "2026-03-31"
    assert top["has_dimension"] == 0
    assert top["value"] == 40_461_000_000


@pytest.mark.parametrize("keyword", ["Cash", "Equity"])
def test_balance_sheet_facts_are_not_crowded_out(conn, keyword):
    """Instant facts have period_end NULL. Sorting on period_end alone pushed every
    balance below all flow facts, so 'Cash' returned only cash-flow lines."""
    rows = search(conn, keyword)
    assert any(r["period_type"] == "instant" for r in rows), (
        f"no balance-sheet fact in the top 15 for {keyword!r}"
    )


def test_latest_cash_balance_ranks_above_prior_year(conn):
    rows = [r for r in search(conn, "CashAndCashEquivalents") if r["local_name"] == "CashAndCashEquivalents"]
    assert rows[0]["period_instant"] == "2026-03-31"
    assert rows[0]["value"] == 8_982_000_000


def test_summary_uses_current_year_balances(conn):
    """Same NULL-period_end issue: the summary picked the prior-year balance sheet."""
    from src.generation.adhoc_retrieval import retrieve_summary_evidence

    evidence, _ = retrieve_summary_evidence(conn, "VOD")
    facts_part = evidence.split("Longest narrative section")[0]
    assert "as at 2026-03-31" in facts_part
    assert "as at 2025-03-31" not in facts_part
