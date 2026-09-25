"""
Filing overview computed straight from the fact store -- no LLM involved.

Shown immediately after upload so a visitor sees what the filing contains
(framework, period, tagging profile, headline figures) before asking
anything, and gets suggested questions that the filing can actually answer.
"""
from __future__ import annotations

import sqlite3

from .formatting import format_value, label, nice_date, period_text

ENTITY_NAME_CONCEPTS = (
    "EntityCurrentLegalOrRegisteredName", "NameOfReportingEntityOrOtherMeansOfIdentification",
    "EntityCurrentLegalName", "NameOfParentEntity",
)

# (display label, candidate local_names in priority order)
KEY_METRICS = [
    ("Revenue", ["Revenue", "TurnoverRevenue", "InsuranceRevenue", "RevenueFromSaleOfGoods"]),
    ("Operating profit", ["ProfitLossFromOperatingActivities", "OperatingProfitLoss"]),
    ("Profit before tax", ["ProfitLossBeforeTax", "ProfitLossOnOrdinaryActivitiesBeforeTax"]),
    ("Profit for the year", ["ProfitLoss"]),
    ("Basic EPS", ["BasicEarningsLossPerShare"]),
    ("Net assets / equity", ["Equity", "NetAssetsLiabilities"]),
    ("Cash", ["CashAndCashEquivalents", "CashBankOnHand"]),
    ("Property, plant & equipment", ["PropertyPlantAndEquipment", "PropertyPlantEquipment"]),
    ("Average employees", ["AverageNumberOfEmployees", "AverageNumberEmployeesDuringPeriod"]),
]

COUNT_HINTS = ("Number", "Employees", "Shares")


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _pick_metric(conn, names: list[str]) -> tuple[dict | None, dict | None]:
    """Current and prior-period undimensioned fact for the first concept present."""
    for name in names:
        rows = _rows(conn, """
            SELECT * FROM facts
            WHERE local_name = ? AND COALESCE(has_dimension,0) = 0 AND COALESCE(is_nil,0) = 0
              AND value IS NOT NULL
            ORDER BY COALESCE(period_end, period_instant) DESC, fact_id
        """, (name,))
        if not rows:
            continue
        # prefer standard taxonomy over extension when both tag the same name
        rows.sort(key=lambda r: (r.get("is_extension") or 0))
        latest_end = max((r.get("period_end") or r.get("period_instant") or "") for r in rows)
        current = next(r for r in rows if (r.get("period_end") or r.get("period_instant") or "") == latest_end)
        prior = next((r for r in rows if (r.get("period_end") or r.get("period_instant") or "") < latest_end), None)
        return current, prior
    return None, None


def build_overview(conn: sqlite3.Connection, filename: str) -> dict:
    name = None
    marks = ",".join("?" * len(ENTITY_NAME_CONCEPTS))
    for table in ("narratives", "facts"):
        col = "text" if table == "narratives" else "raw_text"
        try:
            r = conn.execute(f"SELECT {col} AS v FROM {table} WHERE local_name IN ({marks}) "
                             f"AND {col} IS NOT NULL LIMIT 1", ENTITY_NAME_CONCEPTS).fetchone()
        except sqlite3.OperationalError:
            r = None
        if r and r["v"] and len(r["v"].strip()) < 160:
            name = r["v"].strip()
            break

    prefixes = {r["prefix"]: r["n"] for r in conn.execute(
        "SELECT prefix, COUNT(*) n FROM facts GROUP BY prefix")}
    if prefixes.get("ifrs-full"):
        framework = "IFRS"
    elif prefixes.get("core"):
        framework = "FRS 102 / FRC taxonomy"
    else:
        framework = "Not determined"

    stats = conn.execute("""
        SELECT COUNT(*) n_facts,
               COUNT(DISTINCT context_id) n_contexts,
               SUM(CASE WHEN has_dimension=1 THEN 1 ELSE 0 END) n_dim,
               SUM(CASE WHEN is_extension=1 THEN 1 ELSE 0 END) n_ext,
               COUNT(DISTINCT local_name) n_concepts,
               MAX(COALESCE(period_end, period_instant)) latest
        FROM facts
    """).fetchone()
    n_narr = conn.execute("SELECT COUNT(*) FROM narratives").fetchone()[0]
    currencies = [r[0] for r in conn.execute(
        "SELECT currency FROM facts WHERE currency IS NOT NULL AND currency != '' "
        "GROUP BY currency ORDER BY COUNT(*) DESC LIMIT 3")]

    metrics = []
    for disp, names in KEY_METRICS:
        cur, prior = _pick_metric(conn, names)
        if not cur:
            continue
        per_share = "PerShare" in cur["local_name"]
        item = {
            "label": disp,
            "value": format_value(cur["value"], cur, per_share=per_share),
            "period": period_text(cur),
            "concept": cur.get("concept"),
            "fact_id": cur.get("fact_id"),
        }
        if prior and prior.get("value") not in (None, 0):
            try:
                item["change_pct"] = round((float(cur["value"]) - float(prior["value"]))
                                           / abs(float(prior["value"])) * 100, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        metrics.append(item)

    # Data-quality flags the dissertation found in real filings
    warnings = []
    for r in _rows(conn, "SELECT local_name, value, scale FROM facts WHERE value IS NOT NULL"):
        ln = r["local_name"] or ""
        if any(h in ln for h in COUNT_HINTS) and "PerShare" not in ln:
            try:
                v = float(r["value"])
            except (TypeError, ValueError):
                continue
            if v != int(v):
                warnings.append(f"{label(ln)} is tagged as {v:g} (scale {r['scale']}). A count "
                                "should be a whole number, so the scale attribute may be misapplied "
                                "in the source filing.")
                break

    top = [{"label": label(r["local_name"]), "raw": r["local_name"]} for r in conn.execute(
        "SELECT local_name, COUNT(*) n FROM facts WHERE COALESCE(is_nil,0)=0 "
        "AND COALESCE(taxonomy_family,'') != 'direp' GROUP BY local_name ORDER BY n DESC LIMIT 10")]
    topics = [{"label": label(r["local_name"]), "raw": r["local_name"]} for r in conn.execute(
        "SELECT local_name, MAX(char_count) c FROM narratives GROUP BY local_name "
        "ORDER BY c DESC LIMIT 6")]

    suggestions = []
    for m in metrics[:4]:
        suggestions.append(f"What was {m['label'].lower()} for the {m['period'][0].lower() + m['period'][1:]}?"
                           if m["period"].startswith(("Year", "52", "53")) else
                           f"What was {m['label'].lower()} {m['period'][0].lower() + m['period'][1:]}?")
    if any(m["label"] == "Revenue" and "change_pct" in m for m in metrics):
        suggestions.append("By how much did revenue change compared with the prior year?")
    if topics:
        suggestions.append(f"Summarise the disclosure on {topics[0]['label'].lower()}.")

    n = stats["n_facts"] or 0
    return {
        "filename": filename,
        "entity": name or filename,
        "framework": framework,
        "period_end": nice_date(stats["latest"]),
        "currencies": currencies,
        "n_facts": n,
        "n_narratives": n_narr,
        "n_contexts": stats["n_contexts"] or 0,
        "n_concepts": stats["n_concepts"] or 0,
        "pct_dimensional": round(100 * (stats["n_dim"] or 0) / n) if n else 0,
        "n_extension": stats["n_ext"] or 0,
        "metrics": metrics,
        "warnings": warnings,
        "figures": top,
        "topics": topics,
        "suggestions": suggestions[:5],
    }
