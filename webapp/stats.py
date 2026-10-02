"""
Anonymous usage statistics, so a month of real use can be written up honestly.

What is recorded (one small event per action, never the filing itself):
    visit      a browser opened the site for the first time that day-cookie
    filing     a filing was loaded (upload or sample): company name from the
               filing, framework, fact count, parse time; or why it failed
    question   model, answer kind and badge, response time, estimated cost and
               (if STATS_STORE_QUESTIONS is on) the question and the figure shown
    blocked    a question refused (quota, cap, code needed)
    vote       thumbs up/down on an answer
    unlock     an access code was tried (the code itself is never stored, only
               the first 8 characters of its SHA-256 hash to tell codes apart)

NOT recorded: IP addresses, user agents, the uploaded file or its facts, API keys.
Each browser session gets a random visitor id (an HMAC of the session id that
cannot be turned back into a cookie), which is only used to count visitors and
estimate how long a visit lasted.

Where it is kept:
    * DATABASE_URL set (e.g. a free Neon Postgres): survives restarts and deploys.
    * otherwise a local SQLite file: survives until the server restarts.
Writes happen on a background thread and can never slow down or break a request.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import queue
import sqlite3
import statistics
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("webapp.stats")


def visitor_id(secret: str, sid: str) -> str:
    return hmac.new(secret.encode(), sid.encode(), hashlib.sha256).hexdigest()[:12]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


class StatsStore:
    def __init__(self, settings, *, background: bool = True) -> None:
        self.s = settings
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=5000)
        self._pg = None                               # live Postgres connection (worker thread only)
        self._sqlite_path = (settings.stats_db_path
                             or str(Path(tempfile.gettempdir()) / "tagtrace_stats.sqlite"))
        self.backend = "sqlite"
        self.persistent = False
        if settings.database_url:
            try:
                import psycopg2  # noqa: F401
                self._connect_pg()
                self.backend, self.persistent = "postgres", True
            except Exception as exc:                  # noqa: BLE001
                log.warning("Postgres unavailable (%s); falling back to a local SQLite file", exc)
        if self.backend == "sqlite":
            self._init_sqlite()
        self._thread = None
        if background:
            self._thread = threading.Thread(target=self._run, name="stats-writer", daemon=True)
            self._thread.start()
        else:
            self._sync = True

    _sync = False

    # ---------------------------------------------------------------- backends
    def _connect_pg(self):
        import psycopg2
        self._pg = psycopg2.connect(self.s.database_url, connect_timeout=10)
        self._pg.autocommit = True
        with self._pg.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS events (
                             id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL,
                             kind TEXT NOT NULL, visitor TEXT, data JSONB NOT NULL DEFAULT '{}'::jsonb)""")
            cur.execute("CREATE INDEX IF NOT EXISTS events_ts_idx ON events (ts)")
            cur.execute("CREATE INDEX IF NOT EXISTS events_kind_idx ON events (kind)")

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self._sqlite_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS events (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                           kind TEXT NOT NULL, visitor TEXT, data TEXT NOT NULL DEFAULT '{}')""")
            c.execute("CREATE INDEX IF NOT EXISTS events_ts_idx ON events (ts)")

    def _write(self, ts: float, kind: str, visitor: str | None, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        if self.backend == "postgres":
            for attempt in (1, 2):
                try:
                    if self._pg is None or self._pg.closed:
                        self._connect_pg()
                    with self._pg.cursor() as cur:
                        cur.execute("INSERT INTO events (ts, kind, visitor, data) VALUES (%s,%s,%s,%s::jsonb)",
                                    (ts, kind, visitor, payload))
                    return
                except Exception:                      # noqa: BLE001
                    try:
                        if self._pg is not None:
                            self._pg.close()
                    except Exception:                  # noqa: BLE001
                        pass
                    self._pg = None
                    if attempt == 2:
                        raise
        else:
            with sqlite3.connect(self._sqlite_path, timeout=10) as c:
                c.execute("INSERT INTO events (ts, kind, visitor, data) VALUES (?,?,?,?)",
                          (ts, kind, visitor, payload))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                self._write(*item)
            except Exception:                          # noqa: BLE001
                # Last resort: keep the event in the server log rather than losing it.
                log.warning("stats write failed; event kept in log: %s",
                            json.dumps({"ts": item[0], "kind": item[1], "visitor": item[2], **item[3]},
                                       ensure_ascii=False, default=str))
            finally:
                self._q.task_done()

    # -------------------------------------------------------------------- API
    def record(self, event: str, visitor: str | None = None, /, **data) -> None:
        """Queue one event. Never raises. (Positional-only so data may use any key, e.g. `kind`.)"""
        try:
            data = {k: v for k, v in data.items() if v is not None}
            item = (time.time(), event, visitor, data)
            if self._sync or self._thread is None:
                self._write(*item)
            else:
                self._q.put_nowait(item)
        except Exception:                              # noqa: BLE001
            log.debug("stats record dropped", exc_info=True)

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until queued events are written (used by tests and shutdown)."""
        if self._thread is None:
            return
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)

    def fetch(self, days: int = 30, limit: int = 50000) -> list[dict]:
        since = time.time() - days * 86400
        rows: list[tuple]
        if self.backend == "postgres":
            import psycopg2
            with psycopg2.connect(self.s.database_url, connect_timeout=10) as c, c.cursor() as cur:
                cur.execute("SELECT ts, kind, visitor, data FROM events WHERE ts >= %s "
                            "ORDER BY ts ASC LIMIT %s", (since, limit))
                rows = cur.fetchall()
        else:
            with sqlite3.connect(self._sqlite_path, timeout=10) as c:
                rows = c.execute("SELECT ts, kind, visitor, data FROM events WHERE ts >= ? "
                                 "ORDER BY ts ASC LIMIT ?", (since, limit)).fetchall()
        out = []
        for ts, kind, visitor, data in rows:
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = {}
            out.append({"ts": float(ts), "kind": kind, "visitor": visitor, "data": data or {}})
        return out

    def restore_counters(self, code_prefixes: dict[str, str]) -> dict:
        """Counts needed to re-seed the quota ledger after a restart.

        code_prefixes maps the 8-char code id -> full code hash.
        """
        rows = [r for r in self.fetch(days=400) if r["kind"] == "question"]
        today = _day(time.time())
        by_total: Counter = Counter()
        by_today: Counter = Counter()
        g_q, g_usd = 0, 0.0
        for r in rows:
            d = r["data"]
            full = code_prefixes.get(d.get("code_id", ""))
            if d.get("tier") == "locked" and full:
                by_total[full] += 1
                if _day(r["ts"]) == today:
                    by_today[full] += 1
            if _day(r["ts"]) == today:
                g_q += 1
                g_usd += float(d.get("cost_usd") or 0)
        return {"by_code_total": dict(by_total), "by_code_today": dict(by_today),
                "global_q_today": g_q, "global_usd_today": g_usd}


# --------------------------------------------------------------------- reports
def _median(xs):
    return round(statistics.median(xs), 1) if xs else None


def _mean(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


def summarise(events: list[dict], days: int = 30) -> dict:
    """Turn raw events into the numbers for the admin page / the research post."""
    visitors: set[str] = set()
    by_visitor_ts: dict[tuple, list[float]] = defaultdict(list)       # (visitor, day) -> timestamps
    visits = Counter()
    filings = {"ok": 0, "failed": 0, "upload": 0, "sample": 0}
    entities: Counter = Counter()
    frameworks: Counter = Counter()
    parse_s: list[float] = []
    q_by_model: Counter = Counter()
    q_levels: Counter = Counter()
    q_kinds: Counter = Counter()
    latencies: list[float] = []
    cost = 0.0
    per_day: dict[str, Counter] = defaultdict(Counter)
    asked_visitors: set[str] = set()
    loaded_visitors: set[str] = set()
    voted_visitors: set[str] = set()
    q_per_visitor: Counter = Counter()
    votes = Counter()
    vote_by_answer: dict[str, str] = {}
    blocked: Counter = Counter()
    codes: dict[str, dict] = defaultdict(lambda: {"questions": 0, "first": None, "last": None})
    unlock = Counter()
    questions: list[dict] = []

    for e in events:
        k, v, d, ts = e["kind"], e["visitor"], e["data"], e["ts"]
        day = _day(ts)
        if v:
            visitors.add(v)
            by_visitor_ts[(v, day)].append(ts)
        if k == "visit":
            visits[day] += 1
            per_day[day]["visitors"] += 1
        elif k == "filing":
            if d.get("ok", True):
                filings["ok"] += 1
                filings[d.get("source", "upload")] = filings.get(d.get("source", "upload"), 0) + 1
                per_day[day]["filings"] += 1
                if d.get("entity"):
                    entities[d["entity"]] += 1
                if d.get("framework"):
                    frameworks[d["framework"]] += 1
                if d.get("parse_s") is not None:
                    parse_s.append(float(d["parse_s"]))
                if v:
                    loaded_visitors.add(v)
            else:
                filings["failed"] += 1
        elif k == "question":
            q_by_model[d.get("model", "?")] += 1
            q_levels[d.get("level", "?")] += 1
            q_kinds[d.get("kind", "?")] += 1
            if d.get("elapsed_s") is not None:
                latencies.append(float(d["elapsed_s"]))
            cost += float(d.get("cost_usd") or 0)
            per_day[day]["questions"] += 1
            if v:
                asked_visitors.add(v)
                q_per_visitor[v] += 1
            if d.get("tier") == "locked" and d.get("code_id"):
                c = codes[d["code_id"]]
                c["questions"] += 1
                c["first"] = c["first"] or ts
                c["last"] = ts
            questions.append({"ts": ts, "model": d.get("model"), "entity": d.get("entity"),
                              "question": d.get("question"), "answer": d.get("answer"),
                              "concept": d.get("concept"), "level": d.get("level"),
                              "kind": d.get("kind"), "elapsed_s": d.get("elapsed_s"),
                              "answer_id": d.get("answer_id")})
        elif k == "vote":
            votes[d.get("vote", "?")] += 1
            vote_by_answer[d.get("answer_id", "")] = d.get("vote")
            per_day[day]["votes"] += 1
            if v:
                voted_visitors.add(v)
        elif k == "blocked":
            blocked[d.get("reason", "?")] += 1
        elif k == "unlock":
            unlock["ok" if d.get("ok") else "bad"] += 1

    for q in questions:
        q["vote"] = vote_by_answer.get(q.pop("answer_id", None) or "")

    # Visit length: first to last event within a day, only for visitors with 2+ events.
    spans = [(max(t) - min(t)) / 60 for t in by_visitor_ts.values() if len(t) >= 2]
    asked_total = sum(q_by_model.values())
    abstained = q_levels.get("abstained", 0)
    verified = sum(q_levels.get(x, 0) for x in ("verified", "computed", "quoted"))

    series = [{"day": dy, **{k: per_day[dy].get(k, 0) for k in ("visitors", "filings", "questions", "votes")}}
              for dy in sorted(per_day)]
    return {
        "window_days": days,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "visitors": len(visitors), "visits": sum(visits.values()),
            "filings_loaded": filings["ok"], "filings_uploaded": filings.get("upload", 0),
            "filings_sample": filings.get("sample", 0), "filings_failed": filings["failed"],
            "questions": asked_total, "votes_up": votes.get("up", 0), "votes_down": votes.get("down", 0),
            "blocked": sum(blocked.values()), "est_cost_usd": round(cost, 3),
        },
        "funnel": {"visitors": len(visitors), "loaded_a_filing": len(loaded_visitors),
                   "asked_a_question": len(asked_visitors), "voted": len(voted_visitors)},
        "questions": {
            "by_model": dict(q_by_model), "by_badge": dict(q_levels), "by_kind": dict(q_kinds),
            "pct_verified": round(100 * verified / asked_total, 1) if asked_total else None,
            "pct_not_in_data": round(100 * abstained / asked_total, 1) if asked_total else None,
            "per_asking_visitor": _mean(list(q_per_visitor.values())),
            "median_response_s": _median(latencies), "mean_response_s": _mean(latencies),
        },
        "visit_minutes": {"median": _median(spans), "mean": _mean(spans), "visitors_counted": len(spans)},
        "parse_seconds": {"median": _median(parse_s), "max": round(max(parse_s), 1) if parse_s else None},
        "frameworks": dict(frameworks),
        "top_filings": entities.most_common(15),
        "blocked_reasons": dict(blocked),
        "unlock_attempts": dict(unlock),
        "codes": {k: {"questions": v["questions"], "first": v["first"], "last": v["last"]}
                  for k, v in codes.items()},
        "per_day": series,
        "recent_questions": list(reversed(questions))[:60],
    }


def to_csv(events: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "event", "visitor", "details_json"])
    for e in events:
        w.writerow([datetime.fromtimestamp(e["ts"], timezone.utc).isoformat(timespec="seconds"),
                    e["kind"], e["visitor"] or "", json.dumps(e["data"], ensure_ascii=False)])
    return buf.getvalue()
