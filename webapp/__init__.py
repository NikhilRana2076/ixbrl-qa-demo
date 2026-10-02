"""
Public web demo for the iXBRL hallucination-mitigation system.

    TagTrace. Nikhil Rana, MSc Artificial Intelligence, University of West London
    "Detecting and Mitigating Hallucinations in Large Language Models for
     Financial Document Analysis: A Focus on UK iXBRL Reporting"

Run locally:   flask --app webapp run          (APP_ENV=development)
Production:    gunicorn -c gunicorn.conf.py wsgi:app
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import shutil
import threading
import time
import unicodedata
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session
from markupsafe import Markup
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

try:                                   # gzip/brotli for HTML, CSS and JS
    from flask_compress import Compress
except ImportError:                    # optional: the app still runs without it
    Compress = None

from . import content, ingest
from .answering import answer_question
from .config import Settings, hash_code
from .formatting import dimensions, format_value, label, period_text
from .ingest import ALLOWED_EXT, UploadError, ingest_path, ingest_upload
from .llm import PROVIDERS, make_client
from .overview import build_overview
from .security import SECURITY_HEADERS, RateLimiter, UsageLedger, client_ip, same_origin_ok
from .sessions import FilingRegistry, FilingSession
from .stats import StatsStore, summarise, to_csv, visitor_id

log = logging.getLogger("webapp")
MAX_QUESTION_CHARS = 400


def create_app(settings: Settings | None = None) -> Flask:
    s = settings or Settings.from_env()
    if s.is_production and len(s.secret_key) < 32:
        raise RuntimeError("SECRET_KEY must be set (32+ random characters) in production.")

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=s.secret_key or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=(s.max_upload_mb + 1) * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=s.is_production,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24,
        JSON_SORT_KEYS=False,
    )

    if Compress is not None:
        Compress(app)
    registry = FilingRegistry(s.session_idle_minutes, s.max_live_sessions)
    # Recent answers, so a thumbs-up/down vote can only refer to an answer this
    # session actually received (the client never sends the answer content).
    answers: "OrderedDict[str, dict]" = OrderedDict()
    answers_lock = threading.Lock()
    registry.purge_all()                         # nothing survives a restart
    ledger = UsageLedger(s)
    limiter = RateLimiter()
    stats = StatsStore(s)
    code_ids = {h[:8]: h for h in s.access_codes}
    try:        # keep access-code allowances and today's totals across restarts
        ledger.seed(**stats.restore_counters(code_ids))
    except Exception:                                        # noqa: BLE001
        log.warning("could not restore counters from the stats store", exc_info=True)
    ingest.init_gate(s.max_concurrent_parses)
    sample_lock = threading.Lock()
    sample_cache = Path(registry.root.parent) / "ixbrl_sample_cache"

    app.extensions["demo"] = {"settings": s, "registry": registry, "ledger": ledger, "stats": stats}

    # ------------------------------------------------------------------ helpers
    def sid() -> str:
        if "sid" not in session:
            session["sid"] = secrets.token_urlsafe(24)
            session.permanent = True
        return session["sid"]

    def vid() -> str:
        return visitor_id(app.config["SECRET_KEY"], sid())

    def err(code: str, message: str, status: int = 400, **extra):
        return jsonify({"error": code, "message": message, **extra}), status

    def quota() -> dict:
        q = ledger.status(sid(), client_ip(), session.get("code"))
        q["public_model"] = "GPT-5.6 Terra"
        q["locked_model"] = "Claude Sonnet 4.6"
        return q

    def samples() -> list[dict]:
        d = Path(s.samples_dir)
        if not d.is_dir():
            return []
        return [{"id": p.name, "label": p.stem.replace("_", " ").replace("-", " ")}
                for p in sorted(d.iterdir()) if p.suffix.lower() in ALLOWED_EXT][:6]

    @app.before_request
    def guard():
        host = request.host.split(":")[0].lower()
        if (host in s.redirect_from_hosts and request.path != "/healthz"
                and request.method in ("GET", "HEAD")):
            return redirect(s.site_url + request.full_path.rstrip("?"), code=301)
        if request.method == "POST" and not same_origin_ok():
            return err("forbidden", "Cross-site request blocked.", 403)
        if request.path.startswith("/api/") and not limiter.allow(
                f"all:{client_ip()}", limit=60, window=60):
            return err("rate_limited", "Too many requests. Please slow down.", 429)
        return None

    @app.after_request
    def headers(resp):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if s.is_production:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/api/") or request.path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_e):
        return err("too_large", f"Files are limited to {s.max_upload_mb} MB.", 413)

    @app.errorhandler(Exception)
    def unhandled(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return err("http", e.description or "Request failed.", e.code or 400)
        log.exception("unhandled error")
        return err("server", "Something went wrong on the server. Please try again.", 500)

    # ------------------------------------------------------------------- pages
    @app.get("/")
    def index():
        faq = content.faq(s.public_questions_per_session, s.max_upload_mb,
                          s.session_idle_minutes)
        return render_template("index.html", contact_email=s.contact_email,
                               contact_linkedin=s.contact_linkedin,
                               public_limit=s.public_questions_per_session,
                               max_upload_mb=s.max_upload_mb,
                               site_url=s.site_url, c=content, faq=faq,
                               structured_data=_structured_data(s, faq))

    @app.get("/robots.txt")
    def robots():
        body = f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\n\nSitemap: {s.site_url}/sitemap.xml\n"
        return app.response_class(body, mimetype="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"  <url><loc>{s.site_url}/</loc></url>\n</urlset>\n")
        return app.response_class(body, mimetype="application/xml")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "sessions": len(registry)}

    # --------------------------------------------------------------------- api
    @app.get("/api/state")
    def state():
        if not session.get("seen"):
            session["seen"] = 1
            stats.record("visit", vid())
        item = registry.get(session.get("sid"))
        return jsonify({"quota": quota(), "filing": item.overview if item else None,
                        "samples": samples()})

    @app.get("/api/facts")
    def facts():
        """Every numeric fact in the visitor's filing, for the fact explorer. No LLM involved."""
        item = registry.get(session.get("sid"))
        if not item:
            return err("no_filing", "Load a filing first.")
        if not limiter.allow(f"facts:{client_ip()}", limit=20, window=60):
            return err("rate_limited", "Please slow down.", 429)
        rows = []
        with closing(item.connect()) as conn:
            for r in conn.execute("""
                    SELECT * FROM facts WHERE COALESCE(is_nil,0)=0 AND value IS NOT NULL
                    ORDER BY COALESCE(period_end, period_instant) DESC, has_dimension, local_name
                    LIMIT 8000"""):
                f = dict(r)
                v = format_value(f["value"], f, per_share="PerShare" in (f.get("local_name") or ""))
                rows.append({
                    "id": f["fact_id"], "label": label(f.get("local_name")), "concept": f.get("concept"),
                    "value": v["display"], "exact": v["exact"], "num": f["value"],
                    "period": period_text(f), "dims": [f"{d['axis']}: {d['member']}" for d in dimensions(f)],
                    "ext": bool(f.get("is_extension")),
                })
        return jsonify({"facts": rows, "truncated": len(rows) >= 8000})

    def _finish_load(workdir: Path, db_path: Path, display: str, source: str = "upload",
                     t0: float | None = None):
        item = FilingSession(sid=sid(), workdir=workdir, db_path=db_path, filename=display)
        with closing(item.connect()) as conn:
            item.overview = build_overview(conn, display)
        registry.put(item)
        ov = item.overview
        stats.record("filing", vid(), ok=True, source=source, entity=str(ov.get("entity", ""))[:80],
                     framework=ov.get("framework"), n_facts=ov.get("n_facts"),
                     parse_s=round(time.time() - t0, 1) if t0 else None)
        return jsonify({"filing": item.overview, "quota": quota()})

    @app.post("/api/upload")
    def upload():
        if not limiter.allow(f"up:{client_ip()}", limit=6, window=600):
            return err("rate_limited", "Upload limit reached. Please wait a few minutes.", 429)
        f = request.files.get("file")
        if not f or not f.filename:
            return err("no_file", "Choose a filing to upload.")
        workdir = registry.new_workdir()
        t0 = time.time()
        try:
            db_path, _counts = ingest_upload(f, workdir, s)
        except UploadError as e:
            shutil.rmtree(workdir, ignore_errors=True)
            stats.record("filing", vid(), ok=False, source="upload", reason=str(e)[:100])
            return err("upload", str(e))
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        return _finish_load(workdir, db_path, Path(f.filename).name[:120], "upload", t0)

    @app.post("/api/sample")
    def load_sample():
        name = (request.get_json(silent=True) or {}).get("id", "")
        match = next((x for x in samples() if x["id"] == name), None)
        if not match:
            return err("no_sample", "Unknown sample filing.")
        cached = sample_cache / match["id"] / "facts.sqlite"
        with sample_lock:                      # parse each sample once, then copy
            if not cached.exists():
                tmp = registry.new_workdir()
                (tmp / "incoming").mkdir()
                raw = tmp / "incoming" / match["id"]
                shutil.copy(Path(s.samples_dir) / match["id"], raw)
                try:
                    db_path, _ = ingest_path(raw, tmp, s, display_name=match["id"])
                except UploadError as e:
                    shutil.rmtree(tmp, ignore_errors=True)
                    return err("upload", str(e))
                cached.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(db_path), cached)
                shutil.rmtree(tmp, ignore_errors=True)
        workdir = registry.new_workdir()
        shutil.copy(cached, workdir / "facts.sqlite")
        return _finish_load(workdir, workdir / "facts.sqlite", match["label"], "sample", time.time())

    @app.post("/api/clear")
    def clear():
        registry.drop(session.get("sid"))
        return jsonify({"ok": True, "quota": quota()})

    @app.post("/api/unlock")
    def unlock():
        if not limiter.allow(f"code:{client_ip()}", limit=5, window=900):
            return err("rate_limited", "Too many attempts. Try again in 15 minutes.", 429)
        code = str((request.get_json(silent=True) or {}).get("code", ""))[:64]
        h = hash_code(code)
        if not code or not ledger.check_code(h):
            stats.record("unlock", vid(), ok=False)
            time.sleep(0.4)
            return err("bad_code", "That access code is not valid.")
        stats.record("unlock", vid(), ok=True, code_id=h[:8])
        session["code"] = h
        return jsonify({"ok": True, "quota": quota()})

    @app.post("/api/lock")
    def relock():
        session.pop("code", None)
        return jsonify({"ok": True, "quota": quota()})

    @app.post("/api/ask")
    def ask():
        body = request.get_json(silent=True) or {}
        tier = "locked" if body.get("model") == "locked" else "public"
        question = unicodedata.normalize("NFKC", str(body.get("question", "")))
        question = "".join(ch for ch in question if ch.isprintable()).strip()
        if not question:
            return err("empty", "Type a question first.")
        if len(question) > MAX_QUESTION_CHARS:
            return err("too_long", f"Questions are limited to {MAX_QUESTION_CHARS} characters.")
        item = registry.get(session.get("sid"))
        if not item:
            return err("no_filing", "Load a filing first. Your previous one may have expired after "
                                    f"{s.session_idle_minutes} minutes of inactivity.")
        if not limiter.allow(f"ask:{client_ip()}", limit=s.ask_per_minute, window=60):
            return err("rate_limited", "Please wait a moment between questions.", 429)

        ip, code = client_ip(), session.get("code")
        blocked = ledger.reserve(sid(), ip, tier, code)
        if blocked:
            messages = {
                "service_paused": "The demo has reached today's usage cap. Please try again tomorrow.",
                "locked": "The Claude model needs an access code.",
                "code_exhausted": "This access code has used its question allowance.",
                "code_daily": "This access code has reached its limit for today. It resets at midnight UTC.",
                "session_quota": "You've used all the free questions for this session.",
                "ip_quota": "You've reached today's free question limit.",
            }
            stats.record("blocked", vid(), reason=blocked, tier=tier)
            return err(blocked, messages[blocked], 429 if blocked != "locked" else 403, quota=quota())

        client = make_client(tier, s)
        t0 = time.time()
        try:
            with closing(item.connect()) as conn:
                result = answer_question(conn, item.filename, question, client, tier, PROVIDERS[tier])
        except Exception:                                     # noqa: BLE001
            ledger.refund(sid(), ip, tier, code)
            stats.record("question_failed", vid(), tier=tier)
            log.exception("ask failed (tier=%s)", tier)
            return err("model", "The model could not answer just now. Your question was not counted.",
                       502, quota=quota())
        finally:
            ledger.add_spend(client.spent_usd)

        result.update({
            "question": question,
            "model": "Claude Sonnet 4.6" if tier == "locked" else "GPT-5.6 Terra",
            "tier": tier,
            "elapsed_s": round(time.time() - t0, 1),
            "quota": quota(),
        })
        answer_id = secrets.token_urlsafe(9)
        result["answer_id"] = answer_id
        fact = result.get("fact") or {}
        shown = fact.get("value") or result.get("value")
        shown = shown.get("display") if isinstance(shown, dict) else shown
        with answers_lock:
            answers[answer_id] = {
                "sid": sid(), "tier": tier, "kind": result.get("kind"),
                "level": result.get("status", {}).get("level"),
                "filing": item.filename[:80], "question": question[:200],
                "concept": fact.get("concept"), "value": shown,
                "vote": None,
            }
            while len(answers) > 2000:
                answers.popitem(last=False)
        stats.record("question", vid(), tier=tier, model=result["model"], kind=result.get("kind"),
                     level=result.get("status", {}).get("level") or "n/a",
                     elapsed_s=result["elapsed_s"], cost_usd=round(client.spent_usd, 5),
                     entity=str(item.overview.get("entity", ""))[:80], answer_id=answer_id,
                     code_id=(code or "")[:8] if tier == "locked" else None,
                     **({"question": question[:200], "answer": str(shown)[:80] if shown else None,
                         "concept": fact.get("concept")} if s.stats_store_questions else {}))
        log.info("ask id=%s tier=%s kind=%s status=%s cost=%.5f", answer_id, tier, result.get("kind"),
                 result.get("status", {}).get("level"), client.spent_usd)
        return jsonify(result)

    @app.post("/api/feedback")
    def feedback():
        data = request.get_json(silent=True) or {}
        answer_id, vote = str(data.get("answer_id", ""))[:40], data.get("vote")
        if vote not in ("up", "down"):
            return err("bad_vote", "Vote must be up or down.")
        with answers_lock:
            a = answers.get(answer_id)
            if not a or a["sid"] != session.get("sid"):
                return err("unknown_answer", "That answer can no longer be rated.", 404)
            changed, a["vote"] = a["vote"] != vote, vote
            record = {k: v for k, v in a.items() if k != "sid"}
        if changed:
            stats.record("vote", vid(), answer_id=answer_id, vote=vote, model=a.get("tier"),
                         level=a.get("level"))
            # One JSON line per vote: grep "feedback " in the Render logs to collect them.
            log.info("feedback %s", json.dumps({"answer_id": answer_id, **record}, ensure_ascii=False))
        return jsonify({"ok": True, "vote": vote})

    # ------------------------------------------------------------------- admin
    def _admin_ok():
        """None if the bearer token is right, else an error response. Disabled without ADMIN_TOKEN."""
        if len(s.admin_token) < 16:
            return err("not_found", "Not found.", 404)
        key = f"adminfail:{client_ip()}"
        if limiter.exceeded(key, limit=8, window=900):
            return err("rate_limited", "Too many attempts. Try again later.", 429)
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given.encode(), s.admin_token.encode()):
            limiter.allow(key, limit=8, window=900)          # count the failure
            time.sleep(0.4)
            return err("unauthorised", "Wrong token.", 401)
        return None

    @app.get("/admin")
    def admin_page():
        if len(s.admin_token) < 16:
            return err("not_found", "Not found.", 404)
        return render_template("admin.html", site_url=s.site_url)

    @app.get("/api/admin/stats")
    def admin_stats():
        bad = _admin_ok()
        if bad:
            return bad
        days = min(max(int(request.args.get("days", 30) or 30), 1), 400)
        out = summarise(stats.fetch(days=days), days)
        out["storage"] = {"backend": stats.backend, "persistent": stats.persistent,
                          "stores_questions": s.stats_store_questions}
        return jsonify(out)

    @app.get("/api/admin/export.csv")
    def admin_export():
        bad = _admin_ok()
        if bad:
            return bad
        days = min(max(int(request.args.get("days", 30) or 30), 1), 400)
        resp = app.response_class(to_csv(stats.fetch(days=days)), mimetype="text/csv")
        resp.headers["Content-Disposition"] = "attachment; filename=tagtrace-events.csv"
        return resp

    return app


def _structured_data(s: Settings, faq: list[tuple[str, str]]) -> Markup:
    """schema.org JSON-LD for search engines (WebApplication + FAQPage)."""
    author = {"@type": "Person", "name": "Nikhil Rana", "url": content.PORTFOLIO_URL,
              "sameAs": [u for u in (s.contact_linkedin, content.GITHUB_URL) if u]}
    graph = [
        {"@type": "WebApplication", "name": content.BRAND, "url": s.site_url + "/",
         "description": content.TAGLINE + ". Free demo for UK iXBRL annual reports from Companies House.",
         "applicationCategory": "FinanceApplication", "operatingSystem": "Web",
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "GBP"},
         "author": author},
        {"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]},
    ]
    raw = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    return Markup(raw.replace("</", "<\\/"))
