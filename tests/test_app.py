import io
import zipfile

from conftest import ORIGIN, ask, upload


# ---------------------------------------------------------------- security basics
def test_security_headers_and_no_debug(app, client):
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert not app.debug


def test_cross_site_post_blocked(client):
    r = client.post("/api/clear", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_production_requires_secret(monkeypatch):
    import pytest
    from webapp import create_app
    from webapp.config import Settings
    with pytest.raises(RuntimeError):
        create_app(Settings(env="production", secret_key=""))


# ---------------------------------------------------------------- upload + overview
def test_upload_builds_overview(client):
    r = upload(client)
    assert r.status_code == 200, r.json
    ov = r.json["filing"]
    assert ov["entity"] == "Fictional Test Holdings plc"
    assert ov["framework"] == "IFRS"
    rev = next(m for m in ov["metrics"] if m["label"] == "Revenue")
    assert rev["value"]["display"] == "£4.812bn"
    assert rev["change_pct"] == 6.2
    eps = next(m for m in ov["metrics"] if m["label"] == "Basic EPS")
    assert eps["value"]["display"] == "23.6p"
    assert any("scale" in w for w in ov["warnings"])          # 32.14 employees flagged


def test_rejects_bad_extension_and_sanitises_name(client, tmp_path):
    p = tmp_path / "x.exe"; p.write_bytes(b"MZ")
    assert upload(client, p).status_code == 400
    r = upload(client, name="../../etc/passwd.xhtml")          # traversal attempt
    assert r.status_code == 200 and ".." not in r.json["filing"]["filename"]


def test_zip_slip_and_bomb(client, app, tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.xhtml", "<html/>")
    r = upload(client, z)
    assert r.status_code == 400                                 # skipped -> no document found
    assert not (tmp_path.parent / "escape.xhtml").exists()

    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.xhtml", b"0" * (130 * 1024 * 1024))
    r = upload(client, bomb)
    assert r.status_code == 400 and "size limit" in r.json["message"]


# ---------------------------------------------------------------- answers
def test_fact_answer_value_comes_from_store(client, script):
    upload(client)
    # model summary contains a WRONG number; the card must still show the tagged value
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"], "operation": "none",
                         "summary": "Revenue was £4.9bn."})
    r = ask(client)
    assert r.status_code == 200, r.json
    j = r.json
    assert j["kind"] == "fact" and j["status"]["level"] == "verified"
    assert j["fact"]["value"]["exact"] == "£4,812,000,000"
    assert j["fact"]["period"] == "Year ended 31 December 2025"
    assert any(a["dimensions"] for a in j["alternatives"])     # underlying member surfaced


def test_computation_done_server_side(client, script):
    upload(client)
    script(["Revenue"], {"answer_type": "computation", "fact_ids": ["F1", "F2"],
                         "operation": "difference", "summary": "Revenue rose."})
    j = ask(client, "By how much did revenue change?").json
    assert j["kind"] == "computation" and j["status"]["level"] == "computed"
    assert j["value"]["exact"] == "£282,000,000"


def test_invented_fact_id_is_unverified(client, script):
    upload(client)
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F999"], "summary": "£5bn"})
    j = ask(client).json
    assert j["kind"] == "unverified" and j["status"]["level"] == "unverified"


def test_narrative_quote_checked(client, script):
    upload(client)
    good = "recognised when control passes to the customer, which is on delivery to the customer's site"
    script(["RecognitionOfRevenue"], {"answer_type": "narrative", "narrative_id": "N2",
                                      "quote": good, "summary": "On delivery."})
    assert ask(client, "How is revenue recognised?").json["status"]["level"] == "quoted"
    script(["RecognitionOfRevenue"], {"answer_type": "narrative", "narrative_id": "N2",
                                      "quote": "Revenue is recognised under IFRS 15 in five steps.",
                                      "summary": "Five-step model."})
    j = ask(client, "How is revenue recognised?").json
    assert j["status"]["level"] == "unverified" and j["quote"] == ""


def test_not_found_and_garbage(client, script):
    upload(client)
    script(["Goodwill"], {"answer_type": "not_found"})
    assert ask(client, "What was goodwill?").json["kind"] == "not_found"
    script(["Revenue"], "**£123000**")                         # old markdown-style reply
    assert ask(client).json["kind"] == "unverified"


def test_summary_path(client, script):
    upload(client)
    script("SUMMARY", {})
    j = ask(client, "Give me an overview").json
    assert j["kind"] == "summary" and len(j["metrics"]) >= 4


# ---------------------------------------------------------------- quotas + gate
def test_session_quota(client, script):
    upload(client)
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"]})
    for _ in range(10):
        assert ask(client).status_code == 200
    r = ask(client)
    assert r.status_code == 429 and r.json["error"] == "session_quota"


def test_locked_model_needs_code(client, script):
    upload(client)
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"]})
    assert ask(client, model="locked").status_code == 403
    bad = client.post("/api/unlock", json={"code": "nope"}, headers=ORIGIN)
    assert bad.status_code == 400
    ok = client.post("/api/unlock", json={"code": "good-code"}, headers=ORIGIN)
    assert ok.json["quota"]["unlocked"] and ok.json["quota"]["locked_left"] == 3
    r = ask(client, model="locked")
    assert r.status_code == 200 and r.json["model"] == "Claude Sonnet 4.6"
    assert r.json["quota"]["locked_left"] == 2


def test_provider_failure_refunds(client, script):
    upload(client)
    script(["Revenue"], {}, fail=True)
    r = ask(client)
    assert r.status_code == 502 and "not counted" in r.json["message"]
    assert r.json["quota"]["public_left"] == 10
    assert "provider down" not in r.get_data(as_text=True)     # no internal detail leaked


def test_sessions_are_isolated(app, script):
    a, b = app.test_client(), app.test_client()
    upload(a)
    assert b.get("/api/state").json["filing"] is None
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"]})
    assert ask(b).json["error"] == "no_filing"


def test_global_spend_cap(client, script, app):
    upload(client)
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"]}, cost=0.6)
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200                      # 2 calls x 0.6 x 2 = 2.4 USD (> 2.0 cap)
    r = ask(client)
    assert r.status_code == 429 and r.json["error"] == "service_paused"


def test_feedback_vote_is_tied_to_session(app, client, script, caplog):
    upload(client)
    script(["Revenue"], {"answer_type": "fact", "fact_ids": ["F1"], "operation": "none",
                         "summary": "Revenue was £4.8bn."})
    j = ask(client).json
    aid = j["answer_id"]
    H = {"X-Requested-With": "fetch"}
    assert client.post("/api/feedback", json={"answer_id": aid, "vote": "sideways"}, headers=H).status_code == 400
    with caplog.at_level("INFO", logger="webapp"):
        r = client.post("/api/feedback", json={"answer_id": aid, "vote": "down"}, headers=H)
    assert r.status_code == 200 and r.json["vote"] == "down"
    line = next(m for m in caplog.messages if m.startswith("feedback "))
    assert '"vote": "down"' in line and "revenue" in line.lower() and "sid" not in line
    # another visitor cannot vote on this answer
    other = app.test_client()
    assert other.post("/api/feedback", json={"answer_id": aid, "vote": "up"}, headers=H).status_code == 404
    assert client.post("/api/feedback", json={"answer_id": "nope", "vote": "up"}, headers=H).status_code == 404


def test_seo_routes(client):
    assert b"Sitemap:" in client.get("/robots.txt").data
    assert b"<loc>" in client.get("/sitemap.xml").data
    page = client.get("/").data.decode()
    assert 'application/ld+json' in page and 'data-q="' in page and 'og:image' in page


def test_ratios_quick_asks_and_fact_explorer(client):
    H = {"X-Requested-With": "fetch"}
    assert client.get("/api/facts", headers=H).json["error"] == "no_filing"
    ov = upload(client).json["filing"]
    keys = {r["key"] for r in ov["ratios"]}
    assert "growth" in keys and "net_margin" in keys
    growth = next(r for r in ov["ratios"] if r["key"] == "growth")
    assert growth["display"] == "6.2%" and len(growth["inputs"]) == 2      # 4,812 vs 4,530
    assert any(g["group"] == "Performance" for g in ov["quick_asks"])
    facts = client.get("/api/facts", headers=H).json["facts"]
    assert facts and {"label", "value", "period", "dims", "concept"} <= set(facts[0])
