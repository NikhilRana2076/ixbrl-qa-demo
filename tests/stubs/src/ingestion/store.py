import json, sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (company_number TEXT PRIMARY KEY, company_name TEXT,
  numeric_facts INT, narrative_facts INT);
CREATE TABLE IF NOT EXISTS facts (fact_id INTEGER PRIMARY KEY, company_number TEXT, concept TEXT,
  local_name TEXT, prefix TEXT, taxonomy_family TEXT, is_extension INT, value REAL, raw_text TEXT,
  scale INT, sign TEXT, is_nil INT, unit_label TEXT, currency TEXT, context_id TEXT, period_type TEXT,
  period_start TEXT, period_end TEXT, period_instant TEXT, has_dimension INT, dimension_count INT,
  dimensions_json TEXT);
CREATE TABLE IF NOT EXISTS narratives (narrative_id INTEGER PRIMARY KEY, company_number TEXT,
  concept TEXT, local_name TEXT, taxonomy_family TEXT, is_extension INT, text TEXT, char_count INT,
  continuation_count INT, context_id TEXT, period_start TEXT, period_end TEXT, has_dimension INT,
  dimensions_json TEXT);
"""

def connect(path):
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row; c.executescript(SCHEMA); return c

def insert_company(conn, d):
    conn.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?,?)",
                 (d["company_number"], d["company_name"], d["numeric_facts"], d["narrative_facts"]))

def load_filing(conn, company, filing):
    for f in filing.numeric_facts:
        conn.execute("""INSERT INTO facts (company_number, concept, local_name, prefix, taxonomy_family,
          is_extension, value, raw_text, scale, sign, is_nil, unit_label, currency, context_id, period_type,
          period_start, period_end, period_instant, has_dimension, dimension_count, dimensions_json)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (company, f["concept"], f["concept"].split(":")[1], f["concept"].split(":")[0], f["family"],
           int(f["family"] == "extension"), f["value"], f["raw"], f["scale"], "", 0, f["unit"], f["unit"],
           f["ctx"], f["ptype"], f.get("start"), f.get("end"), f.get("instant"), int(bool(f["dims"])),
           len(f["dims"]), json.dumps(f["dims"]) if f["dims"] else None))
    for n in filing.narrative_facts:
        conn.execute("""INSERT INTO narratives (company_number, concept, local_name, taxonomy_family,
          is_extension, text, char_count, continuation_count, context_id, period_start, period_end,
          has_dimension, dimensions_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (company, n["concept"], n["concept"].split(":")[1], "ifrs", 0, n["text"], len(n["text"]), 0,
           n["ctx"], n.get("start"), n.get("end"), 0, None))
