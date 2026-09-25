"""
src/ingestion/store.py

SQLite fact store for parsed iXBRL filings.

Two tables carry the data: `facts` (numeric tuples) and `narratives` (text
disclosures). Period and dimensional qualifiers are denormalised onto each
row rather than held in a separate contexts table, because the dominant
query pattern — "the value of concept X for company Y in period Z with
dimension D" — is exactly what the tag-aware retriever performs, and joins
would add cost with no analytical benefit.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    company_number   TEXT PRIMARY KEY,
    company_name     TEXT,
    lei              TEXT,
    stratum          TEXT,
    sector           TEXT,
    taxonomy         TEXT,
    currency         TEXT,
    period_start     TEXT,
    period_end       TEXT,
    source           TEXT,
    source_file      TEXT,
    numeric_facts    INTEGER,
    narrative_facts  INTEGER
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_number   TEXT NOT NULL,
    concept          TEXT NOT NULL,
    local_name       TEXT NOT NULL,
    prefix           TEXT,
    namespace        TEXT,
    taxonomy_family  TEXT,
    is_extension     INTEGER,
    value            REAL,
    raw_text         TEXT,
    scale            INTEGER,
    decimals         TEXT,
    sign             TEXT,
    is_nil           INTEGER,
    unit_id          TEXT,
    unit_label       TEXT,
    currency         TEXT,
    context_id       TEXT,
    period_type      TEXT,
    period_start     TEXT,
    period_end       TEXT,
    period_instant   TEXT,
    has_dimension    INTEGER,
    dimension_count  INTEGER,
    dimensions_json  TEXT,
    parse_error      TEXT,
    FOREIGN KEY (company_number) REFERENCES companies(company_number)
);

CREATE TABLE IF NOT EXISTS narratives (
    narrative_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_number     TEXT NOT NULL,
    concept            TEXT,
    local_name         TEXT,
    namespace          TEXT,
    taxonomy_family    TEXT,
    is_extension       INTEGER,
    text               TEXT,
    char_count         INTEGER,
    continuation_count INTEGER,
    context_id         TEXT,
    period_start       TEXT,
    period_end         TEXT,
    has_dimension      INTEGER,
    dimensions_json    TEXT,
    FOREIGN KEY (company_number) REFERENCES companies(company_number)
);

CREATE INDEX IF NOT EXISTS idx_facts_company_local
    ON facts(company_number, local_name);
CREATE INDEX IF NOT EXISTS idx_facts_local
    ON facts(local_name);
CREATE INDEX IF NOT EXISTS idx_facts_company_period
    ON facts(company_number, period_end);
CREATE INDEX IF NOT EXISTS idx_facts_concept
    ON facts(concept);
CREATE INDEX IF NOT EXISTS idx_facts_family
    ON facts(taxonomy_family);
CREATE INDEX IF NOT EXISTS idx_narr_company_local
    ON narratives(company_number, local_name);
"""


def taxonomy_family(namespace: str, is_extension: bool) -> str:
    """Classify a concept's source taxonomy.

    direp is checked before frs because the Directors' Report taxonomy lives
    under the same frc.org.uk domain as the financial taxonomies.
    """
    ns = (namespace or "").lower()
    if "direp" in ns:
        return "direp"
    if "ifrs" in ns:
        return "ifrs"
    if "frc.org.uk" in ns:
        return "frs"
    if is_extension:
        return "extension"
    return "other"


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def clear_company(conn: sqlite3.Connection, company_number: str) -> None:
    """Remove a company's rows so a reload never duplicates."""
    conn.execute("DELETE FROM facts WHERE company_number = ?", (company_number,))
    conn.execute("DELETE FROM narratives WHERE company_number = ?",
                 (company_number,))
    conn.execute("DELETE FROM companies WHERE company_number = ?",
                 (company_number,))


def insert_company(conn: sqlite3.Connection, meta: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO companies
           (company_number, company_name, lei, stratum, sector, taxonomy,
            currency, period_start, period_end, source, source_file,
            numeric_facts, narrative_facts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta.get("company_number"), meta.get("company_name"), meta.get("lei"),
         meta.get("stratum"), meta.get("sector"), meta.get("taxonomy"),
         meta.get("currency"), meta.get("period_start"), meta.get("period_end"),
         meta.get("source"), meta.get("source_file"),
         meta.get("numeric_facts"), meta.get("narrative_facts")),
    )


def _currency_of(unit) -> str:
    if unit is None:
        return ""
    measures = unit.measures + unit.divide_numerator
    for m in measures:
        if m.startswith("iso4217:"):
            return m.split(":", 1)[1].upper()
    return ""


def load_filing(conn: sqlite3.Connection, company_number: str, filing) -> tuple[int, int]:
    """Insert all facts and narratives for one parsed filing."""
    fact_rows = []
    for f in filing.numeric_facts:
        ctx = filing.contexts.get(f.context_id)
        unit = filing.units.get(f.unit_id)
        dims = ctx.dimensions if ctx else {}
        prefix = f.concept.split(":", 1)[0] if ":" in f.concept else ""

        fact_rows.append((
            company_number, f.concept, f.local_name, prefix, f.namespace,
            taxonomy_family(f.namespace, f.is_extension),
            int(f.is_extension), f.value, f.raw_text, f.scale, f.decimals,
            f.sign, int(f.is_nil), f.unit_id,
            unit.label() if unit else "", _currency_of(unit),
            f.context_id,
            ctx.period_type if ctx else "unknown",
            ctx.start_date if ctx else None,
            ctx.end_date if ctx else None,
            ctx.instant if ctx else None,
            int(bool(dims)), len(dims),
            json.dumps(dims, ensure_ascii=False) if dims else None,
            f.parse_error,
        ))

    conn.executemany(
        """INSERT INTO facts
           (company_number, concept, local_name, prefix, namespace,
            taxonomy_family, is_extension, value, raw_text, scale, decimals,
            sign, is_nil, unit_id, unit_label, currency, context_id,
            period_type, period_start, period_end, period_instant,
            has_dimension, dimension_count, dimensions_json, parse_error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        fact_rows,
    )

    narr_rows = []
    for n in filing.narrative_facts:
        ctx = filing.contexts.get(n.context_id)
        dims = ctx.dimensions if ctx else {}
        narr_rows.append((
            company_number, n.concept, n.local_name, n.namespace,
            taxonomy_family(n.namespace, n.is_extension),
            int(n.is_extension), n.text, len(n.text), n.continuation_count,
            n.context_id,
            ctx.start_date if ctx else None,
            ctx.end_date if ctx else None,
            int(bool(dims)),
            json.dumps(dims, ensure_ascii=False) if dims else None,
        ))

    conn.executemany(
        """INSERT INTO narratives
           (company_number, concept, local_name, namespace, taxonomy_family,
            is_extension, text, char_count, continuation_count, context_id,
            period_start, period_end, has_dimension, dimensions_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        narr_rows,
    )

    return len(fact_rows), len(narr_rows)