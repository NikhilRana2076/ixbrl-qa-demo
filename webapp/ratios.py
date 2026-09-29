"""
Key ratios computed straight from the tagged facts -- no LLM involved.

Every ratio records the exact facts it used (fact IDs, values, periods), so the
page can show "Operating profit £5.2bn ÷ Revenue £37.4bn" under each number.
Only undimensioned (consolidated/total) facts are used, and numerator and
denominator must come from the same reporting date. If anything needed is
missing, that ratio is simply left out rather than guessed.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .formatting import format_value, period_text

# Candidate concepts, IFRS first then FRS 102 (core); first one present wins.
C = {
    "revenue": ["Revenue", "TurnoverRevenue", "RevenueFromSaleOfGoods", "RevenueFromContractsWithCustomers"],
    "gross": ["GrossProfit", "GrossProfitLoss"],
    "op": ["ProfitLossFromOperatingActivities", "OperatingProfitLoss"],
    "pbt": ["ProfitLossBeforeTax", "ProfitLossOnOrdinaryActivitiesBeforeTax"],
    "profit": ["ProfitLoss"],
    "equity": ["Equity", "NetAssetsLiabilities"],
    "cash": ["CashAndCashEquivalents", "CashBankOnHand"],
    "ca": ["CurrentAssets"],
    "cl": ["CurrentLiabilities"],
    "nca": ["NetCurrentAssetsLiabilities"],
    "borrowings": ["Borrowings"],
    "borrowings_cur": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "CurrentBorrowings",
                       "ShorttermBorrowings"],
    "borrowings_noncur": ["NoncurrentPortionOfNoncurrentBorrowings", "NoncurrentBorrowings", "LongtermBorrowings"],
    "ocf": ["CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities",
            "NetCashGeneratedFromOperations"],
}


def _day(row: dict) -> date | None:
    s = row.get("period_instant") or row.get("period_end")
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _series(conn: sqlite3.Connection, key: str) -> dict[date, dict]:
    """{reporting date: fact} for the first candidate concept that is tagged."""
    for name in C[key]:
        rows = [dict(r) for r in conn.execute("""
            SELECT * FROM facts
            WHERE local_name = ? AND COALESCE(has_dimension,0) = 0 AND COALESCE(is_nil,0) = 0
              AND value IS NOT NULL
            ORDER BY COALESCE(is_extension,0), fact_id
        """, (name,))]
        out: dict[date, dict] = {}
        for r in rows:
            d = _day(r)
            if d and d not in out:                       # first = non-extension, earliest id
                out[d] = r
        if out:
            return out
    return {}


def _at(series: dict[date, dict], d: date) -> dict | None:
    """Fact on the same reporting date. Instants are often stored a day late, so allow ±1 day."""
    for cand, row in series.items():
        if abs((cand - d).days) <= 1:
            return row
    return None


def _money(v: float, like: dict) -> str:
    return format_value(v, like)["display"]


def _pct(v: float) -> str:
    return f"{v:,.1f}%" if v is not None else "—"


def _src(row: dict, name: str) -> dict:
    return {"name": name, "fact_id": row["fact_id"], "concept": row.get("concept"),
            "display": format_value(row["value"], row)["display"]}


def _same_ccy(*rows: dict) -> bool:
    cc = {r.get("currency") or "" for r in rows}
    return len(cc) == 1


def build_ratios(conn: sqlite3.Connection) -> list[dict]:
    s = {k: _series(conn, k) for k in C}
    base = s["revenue"] or s["profit"] or s["equity"]
    if not base:
        return []
    dates = sorted(base, reverse=True)
    cur_d = dates[0]
    prior_d = dates[1] if len(dates) > 1 else None
    out: list[dict] = []

    def margin(key, label, num_key, num_name):
        vals = {}
        for d in (cur_d, prior_d):
            if d is None:
                continue
            n, r = _at(s[num_key], d), _at(s["revenue"], d)
            if n and r and r["value"] and _same_ccy(n, r):
                vals[d] = (n["value"] / r["value"] * 100, n, r)
        if cur_d not in vals:
            return
        v, n, r = vals[cur_d]
        item = {"key": key, "label": label, "display": _pct(v), "value": v,
                "formula": f"{num_name} {_src(n, '')['display']} ÷ Revenue {_src(r, '')['display']}",
                "period": period_text(n), "inputs": [_src(n, num_name), _src(r, "Revenue")]}
        if prior_d in vals:
            item["change_pp"] = round(v - vals[prior_d][0], 1)
        out.append(item)

    # --- growth
    if s["revenue"] and prior_d:
        r1, r0 = _at(s["revenue"], cur_d), _at(s["revenue"], prior_d)
        if r1 and r0 and r0["value"] and _same_ccy(r1, r0):
            g = (r1["value"] - r0["value"]) / abs(r0["value"]) * 100
            out.append({"key": "growth", "label": "Revenue growth", "display": _pct(g), "value": g,
                        "formula": f"{_src(r1, '')['display']} vs {_src(r0, '')['display']} a year earlier",
                        "period": period_text(r1), "inputs": [_src(r1, "Revenue"), _src(r0, "Revenue (prior)")]})

    # --- profitability
    margin("gross_margin", "Gross margin", "gross", "Gross profit")
    margin("op_margin", "Operating margin", "op", "Operating profit")
    margin("net_margin", "Net margin", "profit", "Profit for the year")

    # --- return on equity (profit ÷ closing equity)
    p, e = _at(s["profit"], cur_d), _at(s["equity"], cur_d)
    if p and e and e["value"] and e["value"] > 0 and _same_ccy(p, e):
        v = p["value"] / e["value"] * 100
        out.append({"key": "roe", "label": "Return on equity", "display": _pct(v), "value": v,
                    "formula": f"Profit {_src(p, '')['display']} ÷ closing equity {_src(e, '')['display']}",
                    "period": period_text(p), "inputs": [_src(p, "Profit for the year"), _src(e, "Equity")]})

    # --- liquidity: current ratio
    ca = _at(s["ca"], cur_d)
    cl = _at(s["cl"], cur_d)
    cl_value, cl_note = (abs(cl["value"]), "current liabilities") if cl else (None, "")
    if ca and not cl:
        nca = _at(s["nca"], cur_d)
        if nca:
            cl_value, cl_note = ca["value"] - nca["value"], "current liabilities (current assets − net current assets)"
            cl = nca
    if ca and cl_value and cl_value > 0 and _same_ccy(ca, cl):
        v = ca["value"] / cl_value
        out.append({"key": "current_ratio", "label": "Current ratio", "display": f"{v:,.2f}×",
                    "value": v, "formula": f"Current assets {_src(ca, '')['display']} ÷ {cl_note} {_money(cl_value, ca)}",
                    "period": period_text(ca), "inputs": [_src(ca, "Current assets"), _src(cl, "Liabilities input")]})

    # --- net cash / (debt) from the Borrowings tag(s)
    cash = _at(s["cash"], cur_d)
    debt_rows = []
    b = _at(s["borrowings"], cur_d)
    if b:
        debt_rows = [b]
    else:
        # need both halves; one on its own would understate debt
        parts = [_at(s["borrowings_cur"], cur_d), _at(s["borrowings_noncur"], cur_d)]
        debt_rows = parts if all(parts) else []
    if cash and debt_rows and _same_ccy(cash, *debt_rows):
        debt = sum(abs(r["value"]) for r in debt_rows)
        v = cash["value"] - debt
        out.append({"key": "net_cash", "label": "Net cash" if v >= 0 else "Net debt",
                    "display": _money(abs(v), cash), "value": v,
                    "formula": f"Cash {_src(cash, '')['display']} − borrowings {_money(debt, cash)} (as tagged; companies differ on whether leases are included)",
                    "period": period_text(cash),
                    "inputs": [_src(cash, "Cash")] + [_src(r, "Borrowings") for r in debt_rows]})

    # --- cash conversion (operating cash flow ÷ operating profit)
    o, op = _at(s["ocf"], cur_d), _at(s["op"], cur_d)
    if o and op and op["value"] and op["value"] > 0 and _same_ccy(o, op):
        v = o["value"] / op["value"] * 100
        out.append({"key": "cash_conversion", "label": "Cash conversion", "display": _pct(v), "value": v,
                    "formula": f"Operating cash flow {_src(o, '')['display']} ÷ operating profit {_src(op, '')['display']}",
                    "period": period_text(o), "inputs": [_src(o, "Operating cash flow"), _src(op, "Operating profit")]})

    order = ["growth", "gross_margin", "op_margin", "net_margin", "roe", "cash_conversion", "current_ratio", "net_cash"]
    out.sort(key=lambda r: order.index(r["key"]))
    return out
