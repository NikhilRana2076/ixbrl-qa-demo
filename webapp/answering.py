"""
Structured, verifiable answers.

The local demo returned the model's free text (e.g. "**£123000**"). Here the
model is asked to return JSON that CITES the evidence IDs it used, and the
server does everything else:

  * fact         -> the value, unit, period and dimensions shown on the card
                    are read from the fact store row the model cited, never
                    re-typed by the model (removes transcription errors such
                    as the EPS rounding in Appendix F, Q026);
  * computation  -> the model only chooses the operation and the input
                    fact IDs; the arithmetic is done here, deterministically;
  * narrative    -> the model's supporting quote must appear verbatim in the
                    cited narrative, otherwise the card is marked unverified;
  * not_found    -> shown as an honest abstention, as in the study.

Every card carries a verification status, and it only says "verified" when
the server has actually checked the claim against the store. Model text
is never trusted as HTML (the browser renders it with textContent).

Retrieval reuses the C3 functions from src.generation.adhoc_retrieval. The
evidence block and output contract are a structured-output variant of the
C3 prompt (Appendix E) - this demo is NOT the benchmarked condition, and the
About page says so.
"""
from __future__ import annotations

import json
import re
import sqlite3

from .formatting import dimensions, format_ratio, format_value, label, period_text, taxonomy_badge
from .overview import build_overview

MAX_FACTS = 20
NARRATIVE_WINDOW = 1400
NARRATIVE_WINDOWS = 2
OPS = {"difference", "sum", "ratio", "percent_change", "percent_of"}

PROMPT = """You are analysing a UK company's annual report and accounts. \
You have been given structured, tagged data retrieved from the filing's \
XBRL facts. Answer the question using ONLY this retrieved evidence. Every \
item has an ID in square brackets (F = numeric fact, N = narrative).

If more than one fact is shown and they represent different dimensional \
breakdowns, choose the one that matches what the question specifically asks \
for. If the question does not specify a dimension, prefer the undimensioned \
(consolidated total) fact. If no undimensioned total exists but one \
dimensional member clearly represents the overall/group figure, use it and \
say so. If the evidence does not answer the question, use answer_type \
"not_found" rather than guessing.

Respond with ONE JSON object and nothing else:
{{
  "answer_type": "fact" | "computation" | "narrative" | "not_found",
  "fact_ids": [<IDs of the F items used, e.g. "F1577">],
  "operation": "none" | "difference" | "sum" | "ratio" | "percent_change" | "percent_of",
  "narrative_id": "<N id or empty>",
  "quote": "<for narrative answers: a short sentence copied EXACTLY from the N item>",
  "summary": "<one or two plain sentences answering the question; no markdown>"
}}
Rules: "fact" cites exactly one F id. "computation" cites the input F ids in \
order (for difference and percent_change: [later_or_minuend, earlier_or_subtrahend]; \
for ratio/percent_of: [numerator, denominator]); do NOT do the arithmetic \
yourself in fact_ids. Never invent IDs.

=== RETRIEVED EVIDENCE ({company_name}) ===
{evidence}
=== END RETRIEVED EVIDENCE ===

Question: {question}

JSON:"""


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------
def _as_dict(row) -> dict:
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    return dict(vars(row))


def _hydrate_facts(conn, facts: list) -> list[dict]:
    """Make sure every retrieved fact is a full facts-table row with fact_id."""
    out, seen = [], set()
    for f in facts:
        d = _as_dict(f)
        fid = d.get("fact_id")
        if fid is None and d.get("concept"):
            r = conn.execute("SELECT * FROM facts WHERE concept=? AND context_id IS ? AND value IS ? LIMIT 1",
                             (d.get("concept"), d.get("context_id"), d.get("value"))).fetchone()
            d = dict(r) if r else d
            fid = d.get("fact_id")
        elif fid is not None:
            r = conn.execute("SELECT * FROM facts WHERE fact_id=?", (fid,)).fetchone()
            d = dict(r) if r else d
        if fid is None or fid in seen:
            continue
        seen.add(fid)
        out.append(d)
    # undimensioned first, then most recent period -- helps the model and the UI
    out.sort(key=lambda d: str(d.get("period_end") or d.get("period_instant") or ""), reverse=True)
    out.sort(key=lambda d: d.get("has_dimension") or 0)          # stable: keeps period order
    return out[:MAX_FACTS]


def _hydrate_narratives(conn, narratives: list) -> list[dict]:
    out = []
    for n in narratives:
        d = _as_dict(n)
        nid = d.get("narrative_id")
        if nid is None:
            r = conn.execute("SELECT * FROM narratives WHERE concept=? LIMIT 1",
                             (d.get("concept"),)).fetchone()
            d = dict(r) if r else d
        elif "text" not in d:
            r = conn.execute("SELECT * FROM narratives WHERE narrative_id=?", (nid,)).fetchone()
            d = dict(r) if r else d
        if d.get("narrative_id") is not None:
            out.append(d)
    return out[:3]


STOP = set("what which when where were was the a an of for in on to and or by did does do is are how much many "
           "company group year ended period as at total its their this that with from".split())


def best_windows(text: str, question: str, width: int = NARRATIVE_WINDOW, k: int = NARRATIVE_WINDOWS) -> str:
    """Pick the passages with the highest keyword density, not just the first N chars.

    The dissertation's C3 truncated narratives at the start and a
    first-keyword-match window did not fix it (decision log 2026-08-24);
    scoring ALL windows by distinct-keyword coverage is the suggested next step.
    """
    if len(text) <= width * k:
        return text
    words = {w for w in re.findall(r"[a-z0-9.%]+", question.lower()) if w not in STOP and len(w) > 2}
    if not words:
        return text[: width * k]
    step = width // 2
    scored = []
    low = text.lower()
    for start in range(0, max(1, len(text) - width + step), step):
        chunk = low[start:start + width]
        distinct = sum(1 for w in words if w in chunk)
        hits = sum(chunk.count(w) for w in words)
        digits = len(re.findall(r"\d", chunk)) / 200.0
        scored.append((distinct * 10 + hits + digits, start))
    scored.sort(reverse=True)
    chosen = []
    for _, s in scored:
        if all(abs(s - c) >= width for c in chosen):
            chosen.append(s)
        if len(chosen) == k:
            break
    chosen.sort()
    return "\n[...]\n".join(text[s:s + width].strip() for s in chosen)


def build_evidence(facts: list[dict], narratives: list[dict], question: str) -> tuple[str, dict]:
    lines = []
    for f in facts:
        dims = dimensions(f)
        dim_txt = "; ".join(f"{d['axis']} = {d['member']}" for d in dims) or "none (consolidated total)"
        per = (f"{f.get('period_start')} to {f.get('period_end')}" if f.get("period_start")
               else f"instant {f.get('period_instant') or f.get('period_end')}")
        unit = f.get("currency") or f.get("unit_label") or ""
        lines.append(f"[F{f['fact_id']}] {f.get('concept')} | value = {f.get('value')} {unit} "
                     f"| period: {per} | dimensions: {dim_txt}")
    excerpts = {}
    for n in narratives:
        ex = best_windows(n.get("text") or "", question)
        excerpts[n["narrative_id"]] = ex
        lines.append(f"[N{n['narrative_id']}] {n.get('concept')}:\n{ex}")
    return ("\n".join(lines) if lines else "(no tagged evidence matched this question)"), excerpts


# --------------------------------------------------------------------------
# model output parsing + verification
# --------------------------------------------------------------------------
def parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            try:
                return json.loads(text[start:i + 1])
            except json.JSONDecodeError:
                return None
    return None


def _ids(values, prefix: str) -> list[int]:
    out = []
    for v in values or []:
        m = re.fullmatch(rf"\s*{prefix}?(\d+)\s*", str(v))
        if m:
            out.append(int(m.group(1)))
    return out


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def fact_card(f: dict) -> dict:
    per_share = "PerShare" in (f.get("local_name") or "")
    return {
        "fact_id": f.get("fact_id"),
        "concept": f.get("concept"),
        "label": label(f.get("local_name")),
        "value": format_value(f.get("value"), f, per_share=per_share),
        "raw_value": f.get("value"),
        "period": period_text(f),
        "dimensions": dimensions(f),
        "taxonomy": taxonomy_badge(f),
        "context_id": f.get("context_id"),
        "scale": f.get("scale"),
        "displayed_as": f.get("raw_text"),
    }


def _compute(op: str, a: float, b: float) -> float | None:
    if op == "difference":
        return a - b
    if op == "sum":
        return a + b
    if op in ("ratio", "percent_of"):
        return None if b == 0 else (a / b) * (100 if op == "percent_of" else 1)
    if op == "percent_change":
        return None if b == 0 else (a - b) / abs(b) * 100
    return None


OP_TEXT = {"difference": "{a} − {b}", "sum": "{a} + {b}", "ratio": "{a} ÷ {b}",
           "percent_of": "{a} ÷ {b} × 100", "percent_change": "({a} − {b}) ÷ |{b}| × 100"}


def verify(parsed: dict | None, facts: list[dict], narratives: list[dict], excerpts: dict) -> dict:
    by_id = {f["fact_id"]: f for f in facts}
    narr_by_id = {n["narrative_id"]: n for n in narratives}
    if not parsed:
        return {"kind": "unverified", "status": _status("unverified",
                "The model's reply could not be read as a structured answer.")}

    kind = str(parsed.get("answer_type", "")).lower()
    summary = str(parsed.get("summary") or "")[:800]
    fids = _ids(parsed.get("fact_ids"), "F")

    if kind == "not_found":
        return {"kind": "not_found", "summary": summary,
                "status": _status("abstained", "The retrieved tagged evidence does not answer this "
                                  "question, so no figure is given.")}

    if kind == "fact":
        cited = [by_id[i] for i in fids if i in by_id]
        if len(cited) != 1:
            return {"kind": "unverified", "summary": summary, "status": _status(
                "unverified", "The model did not cite exactly one retrieved fact.")}
        f = cited[0]
        card = fact_card(f)
        alts = [fact_card(o) for o in facts if o is not f and o.get("local_name") == f.get("local_name")
                and o.get("value") != f.get("value")
                and (o.get("period_end") or o.get("period_instant")) == (f.get("period_end") or f.get("period_instant"))]
        warn = []
        if f.get("is_nil"):
            warn.append("This fact is tagged as nil in the filing (shown as a dash).")
        return {"kind": "fact", "summary": summary, "fact": card, "alternatives": alts[:4],
                "warnings": warn,
                "status": _status("verified", f"Value read directly from tagged fact F{f['fact_id']}.")}

    if kind == "computation":
        op = str(parsed.get("operation") or "").lower()
        cited = [by_id[i] for i in fids if i in by_id]
        if op not in OPS or len(cited) != 2 or len(fids) != 2:
            return {"kind": "unverified", "summary": summary, "status": _status(
                "unverified", "The calculation could not be traced to two retrieved facts.")}
        a, b = cited
        try:
            result = _compute(op, float(a["value"]), float(b["value"]))
        except (TypeError, ValueError):
            result = None
        if result is None:
            return {"kind": "unverified", "summary": summary,
                    "status": _status("unverified", "The calculation is undefined (division by zero).")}
        warn = []
        ua, ub = (a.get("currency") or a.get("unit_label")), (b.get("currency") or b.get("unit_label"))
        if op in ("difference", "sum") and ua != ub:
            warn.append(f"The inputs are in different units ({ua} vs {ub}).")
        if op == "percent_change" and a.get("local_name") != b.get("local_name"):
            warn.append("Percentage change is computed across two different concepts.")
        if op in ("percent_of", "percent_change"):
            value = format_ratio(result, "percent")
        elif op == "ratio":
            value = format_ratio(result, "ratio")
        else:
            value = format_value(result, a)
        ca, cb = fact_card(a), fact_card(b)
        return {"kind": "computation", "summary": summary, "value": value, "operation": op,
                "formula": OP_TEXT[op].format(a=ca["value"]["exact"], b=cb["value"]["exact"]),
                "inputs": [ca, cb], "warnings": warn,
                "status": _status("computed", "Calculated by the server from two tagged facts; "
                                  "the model chose the inputs, not the arithmetic.")}

    if kind == "narrative":
        nids = _ids([parsed.get("narrative_id")], "N")
        n = narr_by_id.get(nids[0]) if nids else None
        quote = str(parsed.get("quote") or "").strip()
        if not n:
            return {"kind": "unverified", "summary": summary, "status": _status(
                "unverified", "The model did not cite a retrieved narrative disclosure.")}
        found = bool(quote) and len(quote) >= 12 and _norm(quote) in _norm(n.get("text") or "")
        base = {"kind": "narrative", "summary": summary,
                "source": {"narrative_id": n["narrative_id"], "concept": n.get("concept"),
                           "label": label(n.get("local_name")), "chars": n.get("char_count"),
                           "period": period_text(n), "taxonomy": taxonomy_badge(n)},
                "quote": quote if found else ""}
        base["status"] = (_status("quoted", "The supporting sentence appears verbatim in the tagged disclosure.")
                          if found else
                          _status("unverified", "The supporting quote could not be found in the tagged "
                                  "text. Treat this summary with caution."))
        return base

    return {"kind": "unverified", "summary": summary,
            "status": _status("unverified", "Unrecognised answer type.")}


def _status(level: str, detail: str) -> dict:
    labels = {"verified": "Matched to tagged fact", "computed": "Computed from tagged facts",
              "quoted": "Quote found in filing", "unverified": "Not verified — check the filing",
              "abstained": "Not in tagged data"}
    return {"level": level, "label": labels[level], "detail": detail}


def summary_answer(conn, filename: str) -> dict:
    ov = build_overview(conn, filename)
    return {"kind": "summary", "summary": "Headline figures read directly from the filing's tagged facts.",
            "metrics": ov["metrics"],
            "status": _status("verified", "Every figure is read from an undimensioned tagged fact.")}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def answer_question(conn, filename: str, question: str, client, tier: str, provider_cfg: dict) -> dict:
    from src.generation.adhoc_retrieval import (
        extract_search_terms,
        retrieve_by_keywords,
        retrieve_narratives_by_keywords,
    )

    terms = extract_search_terms(client, question, temperature=provider_cfg["temperature"])
    if terms == "SUMMARY":
        out = summary_answer(conn, filename)
        out["evidence"] = []
        return out

    facts = _hydrate_facts(conn, retrieve_by_keywords(conn, terms))
    narratives = _hydrate_narratives(conn, retrieve_narratives_by_keywords(conn, terms))
    evidence, excerpts = build_evidence(facts, narratives, question)

    if not facts and not narratives:
        return {"kind": "not_found", "summary": "", "evidence": [], "search_terms": _terms(terms),
                "status": _status("abstained", "No tagged facts or disclosures matched this question.")}

    prompt = PROMPT.format(company_name=filename, evidence=evidence, question=question)
    result = client.generate(prompt, temperature=provider_cfg["temperature"],
                             max_tokens=provider_cfg["max_tokens"], purpose=f"web_{tier}")
    parsed = parse_json(result.get("text", ""))
    out = verify(parsed, facts, narratives, excerpts)
    out["evidence"] = [fact_card(f) for f in facts]
    out["evidence_narratives"] = [{"narrative_id": n["narrative_id"], "label": label(n.get("local_name")),
                                   "concept": n.get("concept"), "chars": n.get("char_count")}
                                  for n in narratives]
    out["search_terms"] = _terms(terms)
    return out


def _terms(terms) -> list[str]:
    if isinstance(terms, str):
        return [terms]
    try:
        return [str(t) for t in terms][:8]
    except TypeError:
        return []
