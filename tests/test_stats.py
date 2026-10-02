"""Usage statistics, admin page, per-code daily caps and host redirects."""
import os

import pytest
from conftest import ORIGIN, ask, upload

from webapp import create_app
from webapp.config import Settings, hash_code, parse_code_daily
from webapp.stats import StatsStore, summarise, to_csv

H = {"X-Requested-With": "fetch"}
TOKEN = "t" * 32


def make_app(tmp_path, **kw):
    base = dict(env="testing", secret_key="x" * 40, access_codes={hash_code("GOOD-CODE"): 20},
                access_code_daily={hash_code("GOOD-CODE"): 2}, public_questions_per_session=10,
                public_questions_per_ip_per_day=20, samples_dir=str(tmp_path / "samples"),
                ask_per_minute=100, admin_token=TOKEN, stats_db_path=str(tmp_path / "stats.sqlite"))
    base.update(kw)
    a = create_app(Settings(**base))
    a.config["TESTING"] = True
    return a


def stats_of(app):
    st = app.extensions["demo"]["stats"]
    st.flush()
    return st


FACT = {"answer_type": "fact", "fact_ids": ["F1"], "operation": "none", "summary": "Revenue."}


# ------------------------------------------------------------- recording
def test_events_recorded_without_ip_or_file(tmp_path, script):
    app = make_app(tmp_path)
    c = app.test_client()
    c.get("/api/state")
    upload(c)
    script(["Revenue"], FACT)
    j = ask(c).json
    c.post("/api/feedback", json={"answer_id": j["answer_id"], "vote": "up"}, headers=H)
    ev = stats_of(app).fetch()
    kinds = [e["kind"] for e in ev]
    assert kinds == ["visit", "filing", "question", "vote"]
    q = next(e for e in ev if e["kind"] == "question")
    assert q["data"]["question"] == "What was revenue?" and q["data"]["level"] == "verified"
    assert q["data"]["entity"] == "Fictional Test Holdings plc"
    blob = str(ev)
    assert "127.0.0.1" not in blob and "fictional_filing" not in blob      # no IP, no file name
    assert len({e["visitor"] for e in ev}) == 1 and len(ev[0]["visitor"]) == 12


def test_question_text_can_be_switched_off(tmp_path, script):
    app = make_app(tmp_path, stats_store_questions=False)
    c = app.test_client()
    upload(c)
    script(["Revenue"], FACT)
    ask(c)
    q = next(e for e in stats_of(app).fetch() if e["kind"] == "question")
    assert "question" not in q["data"] and "answer" not in q["data"] and q["data"]["model"]


def test_failed_question_and_blocked_are_logged(tmp_path, script):
    app = make_app(tmp_path, public_questions_per_session=1)
    c = app.test_client()
    upload(c)
    script(["Revenue"], {}, fail=True)
    assert ask(c).status_code == 502
    script(["Revenue"], FACT)
    assert ask(c).status_code == 200
    assert ask(c).status_code == 429
    kinds = [e["kind"] for e in stats_of(app).fetch()]
    assert "question_failed" in kinds and "blocked" in kinds


# ------------------------------------------------------------- admin
def test_admin_disabled_without_token(tmp_path):
    app = make_app(tmp_path, admin_token="")
    c = app.test_client()
    assert c.get("/admin").status_code == 404
    assert c.get("/api/admin/stats", headers={"Authorization": "Bearer x"}).status_code == 404


def test_admin_requires_exact_token_and_throttles(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = make_app(tmp_path).test_client()
    assert c.get("/api/admin/stats").status_code == 401
    assert c.get("/api/admin/stats", headers={"Authorization": "Bearer nope"}).status_code == 401
    for _ in range(8):
        c.get("/api/admin/stats", headers={"Authorization": "Bearer nope"})
    # brute-forcing is shut out even if the next guess were right
    assert c.get("/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429


def test_admin_report_and_csv(tmp_path, script):
    app = make_app(tmp_path)
    c = app.test_client()
    upload(c)
    script(["Revenue"], FACT)
    aid = ask(c).json["answer_id"]
    c.post("/api/feedback", json={"answer_id": aid, "vote": "down"}, headers=H)
    stats_of(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    page = c.get("/admin")
    assert page.status_code == 200 and b"noindex" in page.data and "noindex" in page.headers["X-Robots-Tag"]
    r = c.get("/api/admin/stats?days=7", headers=auth).json
    assert r["totals"]["questions"] == 1 and r["totals"]["filings_uploaded"] == 1
    assert r["totals"]["votes_down"] == 1 and r["questions"]["pct_verified"] == 100.0
    assert r["funnel"]["asked_a_question"] == 1
    assert r["recent_questions"][0]["vote"] == "down"
    assert r["storage"]["persistent"] is False
    csv = c.get("/api/admin/export.csv", headers=auth)
    assert csv.mimetype == "text/csv" and b"question" in csv.data
    assert b"Disallow: /admin" in c.get("/robots.txt").data


def test_summarise_visit_length_and_funnel():
    t0 = 1_700_000_000
    ev = [{"ts": t0, "kind": "visit", "visitor": "a", "data": {}},
          {"ts": t0 + 120, "kind": "question", "visitor": "a",
           "data": {"model": "GPT-5.6 Terra", "level": "abstained", "elapsed_s": 4.0, "cost_usd": 0.01}},
          {"ts": t0 + 5, "kind": "visit", "visitor": "b", "data": {}}]
    s = summarise(ev)
    assert s["totals"]["visitors"] == 2 and s["visit_minutes"]["median"] == 2.0
    assert s["visit_minutes"]["visitors_counted"] == 1               # b only had one event
    assert s["questions"]["pct_not_in_data"] == 100.0 and s["questions"]["median_response_s"] == 4.0
    assert "time_utc" in to_csv(ev)


# ------------------------------------------------------------- access-code caps
def test_parse_code_daily():
    h = "a" * 64
    assert parse_code_daily(f"{h}:1000:200", 15) == {h: 200}
    assert parse_code_daily(f"{h}:20", 15) == {h: 15}


def test_code_daily_cap_blocks_then_message(tmp_path, script):
    app = make_app(tmp_path)
    c = app.test_client()
    upload(c)
    script(["Revenue"], FACT)
    c.post("/api/unlock", json={"code": "good-code"}, headers=ORIGIN)
    assert c.get("/api/state").json["quota"]["locked_left"] == 2       # min(20 total, 2 per day)
    assert ask(c, model="locked").status_code == 200
    assert ask(c, model="locked").status_code == 200
    r = ask(c, model="locked")
    assert r.status_code == 429 and r.json["error"] == "code_daily" and "midnight" in r.json["message"]


def test_code_allowance_survives_restart(tmp_path, script):
    first = make_app(tmp_path, access_code_daily={hash_code("GOOD-CODE"): 50},
                     access_codes={hash_code("GOOD-CODE"): 3})
    c = first.test_client()
    upload(c)
    script(["Revenue"], FACT)
    c.post("/api/unlock", json={"code": "good-code"}, headers=ORIGIN)
    for _ in range(3):
        assert ask(c, model="locked").status_code == 200
    stats_of(first)
    # "restart": a brand new app on the same stats file must remember the 3 used questions
    second = make_app(tmp_path, access_code_daily={hash_code("GOOD-CODE"): 50},
                      access_codes={hash_code("GOOD-CODE"): 3})
    c2 = second.test_client()
    upload(c2)
    c2.post("/api/unlock", json={"code": "good-code"}, headers=ORIGIN)
    r = ask(c2, model="locked")
    assert r.status_code == 429 and r.json["error"] == "code_exhausted"


# ------------------------------------------------------------- hosts
def test_old_host_redirects_but_healthz_does_not(tmp_path):
    app = make_app(tmp_path, site_url="https://tagtrace.example.com",
                   redirect_from_hosts=("old.example.com",))
    c = app.test_client()
    r = c.get("/?x=1", headers={"Host": "old.example.com"})
    assert r.status_code == 301 and r.headers["Location"] == "https://tagtrace.example.com/?x=1"
    assert c.get("/healthz", headers={"Host": "old.example.com"}).status_code == 200
    assert c.get("/", headers={"Host": "tagtrace.example.com"}).status_code == 200


# ------------------------------------------------------------- real Postgres (optional)
PG = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not PG, reason="set TEST_DATABASE_URL to test against a real Postgres")
def test_postgres_backend_round_trip(tmp_path):
    s = Settings(env="testing", secret_key="x" * 40, database_url=PG)
    st = StatsStore(s)
    assert st.backend == "postgres" and st.persistent
    st.record("question", "v1", tier="locked", code_id="abcd1234", cost_usd=0.02, model="Claude Sonnet 4.6")
    st.flush()
    mine = [e for e in st.fetch(days=1) if e["data"].get("code_id") == "abcd1234"]
    assert mine and mine[-1]["data"]["cost_usd"] == 0.02
    got = st.restore_counters({"abcd1234": "f" * 64})
    assert got["by_code_total"]["f" * 64] >= 1
    assert got["global_usd_today"] >= 0.02
