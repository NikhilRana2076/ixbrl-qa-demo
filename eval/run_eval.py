"""TagTrace evaluation against the 102-question dissertation benchmark.

Two modes:

  retrieval   Free, deterministic, no API calls. For each question, search the fact
              store with the gold concept's name (as an oracle keyword) and record
              where the gold fact ranks. Catches ranking bugs such as balance-sheet
              facts being pushed out of the top 15.

  answer      End to end through webapp.answering.answer_question, exactly as the
              live site runs it (search-term extraction call, retrieval, structured
              answer, server-side verification), then scored by eval/scoring.py.
              Costs real API money; capped by --max-usd.

Filings live in eval/filings/, one per company, named <company_number>*.xhtml|html|zip.
Parsed stores are cached in eval/.stores/ (rebuilt with --rebuild).

Examples:
  python eval/run_eval.py retrieval
  python eval/run_eval.py answer --subset ci --tier public --max-usd 0.50 --min-accuracy 0.80
  python eval/run_eval.py answer --tier locked --out eval/reports/full_locked.json
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.scoring import score, summarise  # noqa: E402

EVAL_DIR = ROOT / "eval"
BENCHMARK = EVAL_DIR / "benchmark.csv"
FILINGS = EVAL_DIR / "filings"
STORES = EVAL_DIR / ".stores"
FILING_SUFFIXES = {".xhtml", ".html", ".htm", ".zip"}

# Fixed 20-question subset for CI: every question type, both strata, dimensional,
# instant, extension, percentage and ambiguous cases.
CI_SUBSET = [
    "Q001", "Q002", "Q013", "Q019", "Q031", "Q040", "Q055",   # extraction, FTSE 350
    "Q076", "Q077", "Q084", "Q088", "Q098",                   # extraction, FRS 102
    "Q004", "Q009", "Q024", "Q086", "Q101",                   # computation
    "Q018", "Q064", "Q095",                                   # disclosure
]


# --------------------------------------------------------------------------- data
def load_benchmark(path: Path = BENCHMARK, subset: str = "all", ids: list[str] | None = None) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if ids:
        wanted = set(ids)
        rows = [r for r in rows if r["question_id"] in wanted]
    elif subset == "ci":
        wanted = set(CI_SUBSET)
        rows = [r for r in rows if r["question_id"] in wanted]
    return rows


def find_filing(company_number: str, filings_dir: Path = FILINGS) -> Path | None:
    for p in sorted(filings_dir.glob(f"{company_number}*")):
        if p.suffix.lower() in FILING_SUFFIXES:
            return p
    return None


def build_store(company_number: str, filings_dir: Path = FILINGS, stores_dir: Path = STORES,
                rebuild: bool = False) -> Path:
    """Parse one company's filing with the same ingest path the web app uses."""
    from webapp import ingest
    from webapp.config import Settings

    db = stores_dir / f"{company_number}.sqlite"
    if db.exists() and not rebuild:
        return db
    filing = find_filing(company_number, filings_dir)
    if filing is None:
        raise FileNotFoundError(f"no filing for {company_number} in {filings_dir}")
    ingest.init_gate(1)
    work = Path(tempfile.mkdtemp())
    try:
        (work / "incoming").mkdir()
        raw = work / "incoming" / filing.name
        shutil.copy(filing, raw)                     # ingest deletes its input
        built, _ = ingest.ingest_path(raw, work, Settings(max_upload_mb=500, max_unzipped_mb=1000), display_name=filing.name)
        stores_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(built), db)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return db


def open_store(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- retrieval mode
def _local(concept: str) -> str:
    return concept.split(":", 1)[-1]


def _gold_fact_ids(conn, row: dict) -> list[list[int]]:
    """Fact-id candidates for each gold input. Matched on concept + context (extraction)
    or concept + value (computation inputs), so ids from the research database are not needed."""
    if row["question_type"] == "extraction":
        ids = [r[0] for r in conn.execute(
            "SELECT fact_id FROM facts WHERE concept = ? AND context_id = ?",
            (row["concept"], row["gold_context_id"]))]
        return [ids]
    groups = []
    for part in (row.get("evidence_text") or "").split(";"):
        if "=" not in part:
            continue
        concept, value = part.strip().split("=", 1)
        try:
            v = float(value.replace(",", ""))
        except ValueError:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT fact_id FROM facts WHERE concept = ? AND ABS(ABS(value) - ?) < 0.5",
            (concept.strip(), abs(v)))]
        groups.append(ids)
    return groups


def retrieval_check(conn, row: dict, k: int = 15) -> dict:
    from src.generation.adhoc_retrieval import retrieve_by_keywords, retrieve_narratives_by_keywords

    if row["question_type"] == "disclosure":
        hits = retrieve_narratives_by_keywords(conn, [_local(row["concept"])])
        ranks = [i + 1 for i, h in enumerate(hits) if h["concept"] == row["concept"]]
        rank = ranks[0] if ranks else None
        return {"found_in_store": True, "ranks": [rank], "hit": rank is not None}

    groups = _gold_fact_ids(conn, row)
    if not groups or not all(groups):
        return {"found_in_store": False, "ranks": [], "hit": False}
    ranks = []
    for ids in groups:
        concept = conn.execute("SELECT concept FROM facts WHERE fact_id = ?", (ids[0],)).fetchone()[0]
        order = [r["fact_id"] for r in retrieve_by_keywords(conn, [_local(concept)], limit=k)]
        found = [order.index(i) + 1 for i in ids if i in order]
        ranks.append(min(found) if found else None)
    return {"found_in_store": True, "ranks": ranks, "hit": all(r is not None for r in ranks)}


def run_retrieval(rows: list[dict], args) -> dict:
    results = []
    for company, group in _by_company(rows):
        with closing(open_store(build_store(company, rebuild=args.rebuild))) as conn:
            for row in group:
                r = retrieval_check(conn, row, k=args.k)
                results.append({"question_id": row["question_id"], "question_type": row["question_type"],
                                "company_number": company, **r})
    n = len(results)
    missing = [r["question_id"] for r in results if not r["found_in_store"]]
    hits = sum(r["hit"] for r in results)
    top1 = sum(1 for r in results if r["ranks"] and all(x == 1 for x in r["ranks"]))
    summary = {"n": n, f"recall_at_{args.k}": round(hits / n, 4) if n else 0.0,
               "all_inputs_rank_1": round(top1 / n, 4) if n else 0.0,
               "gold_not_in_store": missing}
    return {"mode": "retrieval", "k": args.k, "summary": summary, "results": results}


# --------------------------------------------------------------------------- answer mode
def run_answers(rows: list[dict], args, client=None) -> dict:
    from webapp.answering import answer_question
    from webapp.config import Settings
    from webapp.llm import PROVIDERS, make_client

    if client is None:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")                   # local runs; CI passes keys as env vars
        client = make_client(args.tier, Settings.from_env())
    cfg = PROVIDERS[args.tier]
    results, stopped = [], None
    for company, group in _by_company(rows):
        with closing(open_store(build_store(company, rebuild=args.rebuild))) as conn:
            for row in group:
                if client.spent_usd >= args.max_usd:
                    stopped = f"budget of ${args.max_usd:.2f} reached"
                    break
                t0, answer, err = time.time(), None, None
                try:
                    answer = answer_question(conn, row["company_number"], row["question_text"],
                                             client, args.tier, cfg)
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                s = score(row, answer, err)
                results.append({"question_id": row["question_id"], "question_type": row["question_type"],
                                "company_number": company, **s,
                                "answer_kind": (answer or {}).get("kind"),
                                "search_terms": (answer or {}).get("search_terms"),
                                "seconds": round(time.time() - t0, 2)})
                print(f"{row['question_id']:5} {s['outcome']:10} {s['detail']}", flush=True)
        if stopped:
            break
    return {"mode": "answer", "tier": args.tier, "summary": summarise(results),
            "spent_usd": round(client.spent_usd, 4), "stopped_early": stopped, "results": results}


# --------------------------------------------------------------------------- cli
def _by_company(rows):
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["company_number"], []).append(r)
    return sorted(groups.items())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["retrieval", "answer"])
    p.add_argument("--subset", choices=["all", "ci"], default="all")
    p.add_argument("--ids", nargs="*", help="specific question ids, e.g. Q001 Q004")
    p.add_argument("--tier", choices=["public", "locked"], default="public",
                   help="public = OpenAI model, locked = Claude (as on the live site)")
    p.add_argument("--k", type=int, default=15, help="retrieval depth (the app uses 15)")
    p.add_argument("--max-usd", type=float, default=1.00)
    p.add_argument("--min-accuracy", type=float, help="answer mode: exit 1 below this")
    p.add_argument("--min-recall", type=float, help="retrieval mode: exit 1 below this")
    p.add_argument("--rebuild", action="store_true", help="re-parse filings")
    p.add_argument("--out", type=Path, help="write the JSON report here")
    args = p.parse_args(argv)

    rows = load_benchmark(subset=args.subset, ids=args.ids)
    missing = sorted({r["company_number"] for r in rows if find_filing(r["company_number"]) is None})
    if missing:
        print(f"Missing filings in {FILINGS.relative_to(ROOT)}/ for: {', '.join(missing)}\n"
              "Add one file per company named <company_number>_<anything>.xhtml|html|zip "
              "(download iXBRL from Companies House).")
        return 2
    report = run_retrieval(rows, args) if args.mode == "retrieval" else run_answers(rows, args)

    headline = report["summary"] if args.mode == "retrieval" else report["summary"]["overall"]
    print(json.dumps(headline, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))

    if args.mode == "retrieval" and args.min_recall is not None:
        if report["summary"][f"recall_at_{args.k}"] < args.min_recall:
            print(f"FAIL: recall below {args.min_recall}")
            return 1
    if args.mode == "answer" and args.min_accuracy is not None:
        if report["stopped_early"]:
            print(f"FAIL: {report['stopped_early']} before all questions ran")
            return 1
        if headline["accuracy"] < args.min_accuracy:
            print(f"FAIL: accuracy {headline['accuracy']} below {args.min_accuracy}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
