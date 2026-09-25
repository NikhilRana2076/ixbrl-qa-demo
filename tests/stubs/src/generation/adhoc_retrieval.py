import json

def extract_search_terms(client, question, temperature=None):
    r = client.generate("TERMS:" + question, temperature=temperature, max_tokens=60, purpose="terms")
    t = r["text"].strip()
    return "SUMMARY" if t == "SUMMARY" else json.loads(t)

def retrieve_by_keywords(conn, terms, limit=30):
    out = []
    for t in terms:
        out += conn.execute("SELECT * FROM facts WHERE local_name LIKE ? AND is_nil=0 LIMIT ?",
                            (f"%{t}%", limit)).fetchall()
    return out

def retrieve_narratives_by_keywords(conn, terms, limit=2):
    out = []
    for t in terms:
        out += conn.execute("SELECT * FROM narratives WHERE local_name LIKE ? LIMIT ?", (f"%{t}%", limit)).fetchall()
    return out[:limit]
