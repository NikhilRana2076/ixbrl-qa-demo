"""Human-readable rendering of tagged facts (value, unit, period, dimensions)."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def label(local_name: str | None) -> str:
    """'ProfitLossBeforeTax' -> 'Profit loss before tax'."""
    if not local_name:
        return ""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local_name).split()
    if not words:
        return local_name
    out = [words[0]] + [w if w.isupper() and len(w) > 1 else w.lower() for w in words[1:]]
    return " ".join(out)


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def nice_date(s: str | None) -> str:
    d = _d(s)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}" if d else (s or "")


def period_text(f: dict) -> str:
    if f.get("period_type") == "instant" or f.get("period_instant"):
        # iXBRL instants are often stored as the day AFTER the balance sheet date
        return "As at " + nice_date(f.get("period_instant") or f.get("period_end"))
    start, end = _d(f.get("period_start")), _d(f.get("period_end"))
    if start and end:
        days = (end - start).days + 1                      # inclusive
        if 357 <= days <= 378:
            month_end = (end + timedelta(days=1)).day == 1
            if not month_end and days % 7 == 0:
                return f"{days // 7} weeks ended {nice_date(f.get('period_end'))}"
            return f"Year ended {nice_date(f.get('period_end'))}"
        return f"{nice_date(f.get('period_start'))} to {nice_date(f.get('period_end'))}"
    return nice_date(f.get("period_end")) or ""


def dimensions(f: dict) -> list[dict]:
    raw = f.get("dimensions_json")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    items = data.items() if isinstance(data, dict) else (
        (d.get("axis") or d.get("dimension"), d.get("member")) for d in data if isinstance(d, dict))
    out = []
    for axis, member in items:
        a = str(axis or "").split(":")[-1]
        m = str(member or "").split(":")[-1]
        out.append({
            "axis": label(re.sub(r"(Axis|Dimension)$", "", a)),
            "member": label(re.sub(r"Member$", "", m)),
            "raw": f"{axis} = {member}",
        })
    return out


def unit_kind(f: dict) -> str:
    u = (f.get("currency") or f.get("unit_label") or "").upper()
    if u in SYMBOLS or len(u) == 3 and u.isalpha() and u not in {"PURE"}:
        return "money"
    if "SHARE" in u and "/" not in u and "PER" not in u:
        return "shares"
    if "PURE" in u:
        return "pure"
    return "other"


def _currency(f: dict) -> str:
    return (f.get("currency") or f.get("unit_label") or "").upper()


def format_value(value, f: dict, per_share: bool = False) -> dict:
    """Returns {'display': '£32,812m', 'exact': '£32,812,000,000', 'unit': 'GBP'}."""
    if value is None:
        return {"display": "—", "exact": "—", "unit": ""}
    v = float(value)
    cur = _currency(f)
    kind = unit_kind(f)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if kind == "money":
        sym = SYMBOLS.get(cur, "")
        suffix = "" if sym else f" {cur}"
        if per_share or (a < 100 and a != int(a)):
            if cur == "GBP" and a < 10:
                display = f"{sign}{a * 100:,.2f}".rstrip("0").rstrip(".") + "p"
            else:
                display = f"{sign}{sym}{a:,.4f}".rstrip("0").rstrip(".") + suffix
            exact = f"{sign}{sym}{a:,.4f}".rstrip("0").rstrip(".") + suffix
        elif a >= 1e9:
            display = f"{sign}{sym}{a / 1e9:,.3f}".rstrip("0").rstrip(".") + "bn" + suffix
            exact = f"{sign}{sym}{a:,.0f}{suffix}"
        elif a >= 1e6:
            display = f"{sign}{sym}{a / 1e6:,.1f}".rstrip("0").rstrip(".") + "m" + suffix
            exact = f"{sign}{sym}{a:,.0f}{suffix}"
        else:
            display = exact = f"{sign}{sym}{a:,.0f}{suffix}"
        return {"display": display, "exact": exact, "unit": cur}
    if kind == "shares":
        s = f"{sign}{a:,.0f} shares"
        return {"display": s, "exact": s, "unit": "shares"}
    s = f"{sign}{a:,.4f}".rstrip("0").rstrip(".")
    return {"display": s, "exact": s, "unit": cur.lower()}


def format_ratio(v: float, kind: str) -> dict:
    if kind == "percent":
        s = f"{v:,.2f}%"
    else:
        s = f"{v:,.4f}".rstrip("0").rstrip(".")
    return {"display": s, "exact": s, "unit": kind}


def taxonomy_badge(f: dict) -> str:
    fam = (f.get("taxonomy_family") or "").lower()
    if f.get("is_extension") or fam == "extension":
        return "Company extension"
    return {"ifrs": "IFRS taxonomy", "frs": "FRC (FRS 102) taxonomy",
            "direp": "Directors' report taxonomy"}.get(fam, fam or "Taxonomy")
