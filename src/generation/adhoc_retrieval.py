"""
src/generation/adhoc_retrieval.py

Generalises C3's tag-aware retrieval to arbitrary, previously-unseen
filings. The benchmark's C3 pipeline retrieves by a pre-recorded concept
field; here, a question arrives as free text with no such field, so a
cheap LLM call first extracts candidate XBRL concept-name fragments, which
are then matched against the fact store the same way query_facts.py does.
"""
from __future__ import annotations

import sqlite3

SUMMARY_CONCEPTS = [
    "Revenue", "Turnover", "RevenueFromSaleOfGoods", "CostSales",
    "GrossProfit", "ProfitLossBeforeTax", "ProfitLoss",
    "ProfitLossFromOperatingActivities", "Equity", "Assets",
    "PropertyPlantEquipment", "CashBankOnHand", "CashAndCashEquivalents",
    "AverageNumberEmployeesDuringPeriod", "BasicEarningsLossPerShare",
]

EXTRACT_TEMPLATE = """You are helping search a UK company's structured \
iXBRL financial filing for facts relevant to a question. XBRL concepts \
use names like Revenue, ProfitLoss, CostSales, PropertyPlantEquipment, \
GrossProfit, Equity, Assets, Goodwill, DividendsPaid, Employees, Segments \
-- often with a taxonomy prefix such as ifrs-full: or core:.

Given the question below, list up to 5 short keyword fragments (single \
words or short fragments, not full sentences) likely to appear in the \
XBRL concept name(s) needed to answer it. This applies even to questions \
about accounting policies, disclosures, or narrative topics (e.g. "how is \
revenue recognised" -> revenue, recognition, policy; "what segments does \
the company report" -> segment, reportable).

Only respond with the single word SUMMARY if the question is a broad, \
non-specific request for an overview of the entire filing (e.g. \
"summarise this filing", "give me an overview", "what does this company \
do"), with no particular topic named.

Question: {question}

Keywords (comma-separated, no explanation, or SUMMARY):"""


def extract_search_terms(client, question: str,
                          temperature: float | None = 0.0) -> list[str] | str:
    prompt = EXTRACT_TEMPLATE.format(question=question)
    result = client.generate(prompt, temperature=temperature, max_tokens=400,
                             purpose="adhoc_concept_extraction")
    text = result["text"].strip()
    if text.upper().startswith("SUMMARY"):
        return "SUMMARY"
    terms = [t.strip() for t in text.split(",") if t.strip()]
    return terms[:5]


def retrieve_by_keywords(conn: sqlite3.Connection, keywords: list[str],
                          limit: int = 15) -> list[dict]:
    """Fuzzy concept-name search across the fact store, prioritising
    non-nil, undimensioned, most-recent facts -- the same 'prefer the
    consolidated total' preference used in the C3 prompt."""
    if not keywords:
        return []
    clauses = " OR ".join("lower(local_name) LIKE ?" for _ in keywords)
    params = [f"%{k.lower()}%" for k in keywords]
    sql = (f"SELECT * FROM facts WHERE ({clauses}) AND is_nil = 0 "
           f"ORDER BY has_dimension ASC, COALESCE(period_end, period_instant) DESC, fact_id ASC "
           f"LIMIT ?")
    rows = conn.execute(sql, params + [limit]).fetchall()
    return [dict(r) for r in rows]


def retrieve_narratives_by_keywords(conn: sqlite3.Connection,
                                     keywords: list[str],
                                     limit: int = 2) -> list[dict]:
    if not keywords:
        return []
    clauses = " OR ".join("lower(local_name) LIKE ?" for _ in keywords)
    params = [f"%{k.lower()}%" for k in keywords]
    sql = (f"SELECT * FROM narratives WHERE {clauses} "
           f"ORDER BY char_count DESC LIMIT ?")
    rows = conn.execute(sql, params + [limit]).fetchall()
    return [dict(r) for r in rows]


def retrieve_summary_evidence(conn: sqlite3.Connection,
                               company_number: str) -> tuple[str, int]:
    """Best-effort summary evidence: whichever of a fixed set of common
    headline concepts are actually present, plus the largest narrative
    block (typically accounting policies or strategic overview)."""
    from .retrieval import format_facts_as_evidence

    facts: list[dict] = []
    for concept in SUMMARY_CONCEPTS:
        rows = conn.execute(
            "SELECT * FROM facts WHERE company_number = ? "
            "AND local_name = ? AND is_nil = 0 AND has_dimension = 0 "
            "ORDER BY COALESCE(period_end, period_instant) DESC LIMIT 1",
            (company_number, concept),
        ).fetchall()
        facts.extend(dict(r) for r in rows)

    narrative_row = conn.execute(
        "SELECT * FROM narratives WHERE company_number = ? "
        "ORDER BY char_count DESC LIMIT 1", (company_number,),
    ).fetchone()

    evidence = format_facts_as_evidence(facts, max_facts=14)
    if narrative_row:
        evidence += (f"\n\nLongest narrative section "
                     f"({narrative_row['concept']}):\n"
                     f"{narrative_row['text'][:2000]}")
    return evidence, len(facts)