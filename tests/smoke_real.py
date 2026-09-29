"""
Adapter smoke test against the REAL src/ package and a REAL filing. No API calls.

    python tests/smoke_real.py path/to/filing.xhtml   (or a report-package .zip)

Checks the things the web layer assumes about src/ and prints what it sees:
  1. ingestion writes a file-backed SQLite store via connect(path)
  2. the facts / narratives tables have the Figure 3 columns
  3. retrieve_by_keywords / retrieve_narratives_by_keywords rows can be hydrated
     to full rows with fact_id / narrative_id
  4. the overview builds and the evidence block renders
  5. the lxml parser refuses external entities (XXE)
"""
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp import ingest  # noqa: E402
from webapp.answering import _hydrate_facts, _hydrate_narratives, build_evidence  # noqa: E402
from webapp.config import Settings  # noqa: E402
from webapp.overview import build_overview  # noqa: E402


def main(path: str) -> None:
    import src.ingestion.parser as real_parser
    print("Using src from:", Path(real_parser.__file__).resolve().parent.parent)
    ingest.init_gate(1)
    work = Path(tempfile.mkdtemp())
    (work / "incoming").mkdir()
    raw = work / "incoming" / Path(path).name
    shutil.copy(path, raw)
    db, counts = ingest.ingest_path(raw, work, Settings(), display_name=Path(path).name)
    print("1. parsed:", counts, "->", db.name)

    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        need_f = {"fact_id", "concept", "local_name", "prefix", "taxonomy_family", "is_extension", "value",
                  "raw_text", "scale", "is_nil", "unit_label", "currency", "context_id", "period_type",
                  "period_start", "period_end", "period_instant", "has_dimension", "dimensions_json"}
        need_n = {"narrative_id", "concept", "local_name", "text", "char_count", "context_id"}
        have_f = {r[1] for r in conn.execute("PRAGMA table_info(facts)")}
        have_n = {r[1] for r in conn.execute("PRAGMA table_info(narratives)")}
        print("2. missing fact columns:", sorted(need_f - have_f) or "none",
              "| missing narrative columns:", sorted(need_n - have_n) or "none")

        from src.generation.adhoc_retrieval import retrieve_by_keywords, retrieve_narratives_by_keywords
        facts = _hydrate_facts(conn, retrieve_by_keywords(conn, ["Revenue"]))
        narrs = _hydrate_narratives(conn, retrieve_narratives_by_keywords(conn, ["Revenue"]))
        print(f"3. hydrated {len(facts)} facts, {len(narrs)} narratives; first fact_id =",
              facts[0]["fact_id"] if facts else None)

        ov = build_overview(conn, Path(path).name)
        print("4. overview:", ov["entity"], "|", ov["framework"], "|", ov["period_end"],
              "|", [(m["label"], m["value"]["display"]) for m in ov["metrics"]])
        print("   warnings:", ov["warnings"])
        ev, _ = build_evidence(facts[:3], narrs[:1], "What was revenue?")
        print("   evidence sample:\n  ", ev[:600].replace("\n", "\n   "))

    xxe = work / "xxe.xhtml"
    xxe.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
                   '<html xmlns="http://www.w3.org/1999/xhtml"><body>&e;</body></html>')
    try:
        f = real_parser.parse_filing(xxe)
        leaked = "root:" in str(getattr(f, "narrative_facts", "")) + str(getattr(f, "numeric_facts", ""))
        print("5. XXE:", "LEAKED - set resolve_entities=False, no_network=True in parser!" if leaked else "safe")
    except Exception as exc:  # noqa: BLE001
        print("5. XXE: parser rejected the document (safe):", type(exc).__name__)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1])
