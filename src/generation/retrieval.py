"""
src/generation/retrieval.py

Tag-aware retrieval for C3: given a benchmark question's recorded concept
(and, for disclosure questions, its narrative concept), look up the
matching fact(s) or narrative text directly from the structured store by
concept name -- not by gold_fact_id, which would make retrieval an oracle
rather than a genuine lookup.
"""
from __future__ import annotations

import sqlite3


def _local_name(concept: str) -> str:
    return concept.split(":", 1)[-1] if ":" in concept else concept


def retrieve_facts(conn: sqlite3.Connection, company_number: str,
                    concept: str, period_end: str | None = None) -> list[dict]:
    """All facts for a company matching a concept's local name, optionally
    filtered to a period. Mirrors query_facts.py's `fact` lookup."""
    local = _local_name(concept)
    sql = ("SELECT * FROM facts WHERE company_number = ? "
           "AND local_name = ? AND is_nil = 0")
    params = [company_number, local]
    if period_end:
        sql += " AND period_end = ?"
        params.append(period_end)
    sql += " ORDER BY has_dimension, fact_id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def retrieve_narratives(conn: sqlite3.Connection, company_number: str,
                         concept: str, limit: int = 3) -> list[dict]:
    """Top matching narratives for a company by concept local name,
    broadened from a single best match after manual error review found
    the relevant figure sometimes sits in a different narrative block
    than the single largest match (e.g. Tesco's 77.5th percentile
    confidence level, AstraZeneca's discount rate)."""
    local = _local_name(concept)
    rows = conn.execute(
        "SELECT * FROM narratives WHERE company_number = ? "
        "AND local_name = ? ORDER BY char_count DESC LIMIT ?",
        (company_number, local, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def retrieve_narrative(conn: sqlite3.Connection, company_number: str,
                        concept: str) -> dict | None:
    """Kept for compatibility: single best match."""
    results = retrieve_narratives(conn, company_number, concept, limit=1)
    return results[0] if results else None


def format_facts_as_evidence(facts: list[dict], max_facts: int = 8) -> str:
    """Render retrieved facts as structured, unambiguous evidence text."""
    if not facts:
        return "(no matching tagged facts found in the structured data)"
    lines = []
    for f in facts[:max_facts]:
        period = (f"as at {f['period_instant']}" if f["period_type"] == "instant"
                  else f"{f['period_start']} to {f['period_end']}")
        dims = f" [dimension: {f['dimensions_json']}]" if f["has_dimension"] else ""
        val = "NIL" if f["is_nil"] else f"{f['value']:,.2f}".rstrip("0").rstrip(".")
        lines.append(
            f"- Concept: {f['concept']}\n"
            f"  Value: {val} {f['currency'] or f['unit_label']}\n"
            f"  Period: {period}{dims}"
        )
    return "\n".join(lines)

def extract_relevant_window(text: str, question: str, window: int = 3000) -> str:
    """Return a window of text centred on the first strong keyword match
    from the question, rather than blindly truncating from the start.
    Falls back to the start of the text if no keyword is found.

    This addresses a real limitation found during evaluation: some
    retrieved narratives (up to 90,906 characters) are far too long to
    pass to the model in full, and truncating from position 0 can miss a
    specific figure that appears later in the block (e.g. a percentile or
    discount rate buried deep in an insurance contracts note).
    """
    import re
    stopwords = {"what", "was", "were", "the", "is", "are", "for", "and",
                 "did", "does", "how", "which", "used", "use", "of", "in",
                 "on", "to", "a", "an"}
    words = [w for w in re.findall(r"[a-zA-Z]{4,}", question.lower())
             if w not in stopwords]

    lower_text = text.lower()
    best_pos = None
    for w in words:
        pos = lower_text.find(w)
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos

    if best_pos is None:
        return text[:window]

    start = max(0, best_pos - window // 3)
    end = min(len(text), start + window)
    prefix = "...(truncated)... " if start > 0 else ""
    suffix = " ...(truncated)..." if end < len(text) else ""
    return prefix + text[start:end] + suffix