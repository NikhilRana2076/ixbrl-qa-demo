"""
Abuse and cost controls.

Layers (a request must pass all of them before any paid API call is made):
  1. Rate limiter      - bursts per IP (stops scripted hammering).
  2. Session quota     - public questions per browser session.
  3. IP daily quota    - stops "clear cookies, get 10 more".
  4. Access-code quota - the Claude model only runs for a valid code, and
                         each code carries its own lifetime allowance AND a
                         per-day cap (so a shared code can't be drained at once).
  5. Global daily caps - total questions and estimated USD spend per day
                         across ALL visitors. Your provider-side spend limits
                         are a final backstop behind this one.

Counters are in-process memory and reset on restart/redeploy, EXCEPT the
access-code counts and today's global totals, which are re-seeded from the
usage-statistics store at start-up (see UsageLedger.seed) when one is
configured. Provider-side spend limits remain the final backstop.
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from flask import request


def client_ip() -> str:
    # Render terminates TLS at a proxy; ProxyFix (see __init__) rewrites
    # remote_addr from X-Forwarded-For using exactly one trusted hop.
    return request.remote_addr or "unknown"


class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window` seconds per key."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def exceeded(self, key: str, limit: int, window: float) -> bool:
        """True if `key` is already at its limit. Does not record a hit."""
        now = time.time()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            while q and q[0] <= now - window:
                q.popleft()
            return len(q) >= limit

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 20000:          # bound memory
                for k in [k for k, v in self._hits.items() if not v][:10000]:
                    del self._hits[k]
            return True


class UsageLedger:
    def __init__(self, settings) -> None:
        self.s = settings
        self._lock = threading.Lock()
        self._day = self._today()
        self._by_session: dict[str, int] = defaultdict(int)
        self._by_ip: dict[str, int] = defaultdict(int)
        self._by_code: dict[str, int] = defaultdict(int)   # lifetime allowance used
        self._by_code_day: dict[str, int] = defaultdict(int)
        self._global_q = 0
        self._global_usd = 0.0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._by_session.clear()
            self._by_ip.clear()
            self._by_code_day.clear()
            self._global_q = 0
            self._global_usd = 0.0

    # ---- access codes -------------------------------------------------
    def check_code(self, code_hash: str) -> bool:
        for known in self.s.access_codes:
            if hmac.compare_digest(known, code_hash):
                return True
        return False

    def seed(self, by_code_total: dict, by_code_today: dict, global_q_today: int,
             global_usd_today: float) -> None:
        """Restore counters after a restart from the persisted question log."""
        with self._lock:
            self._roll()
            for k, v in by_code_total.items():
                self._by_code[k] = max(self._by_code[k], int(v))
            for k, v in by_code_today.items():
                self._by_code_day[k] = max(self._by_code_day[k], int(v))
            self._global_q = max(self._global_q, int(global_q_today))
            self._global_usd = max(self._global_usd, float(global_usd_today))

    def _code_left_locked(self, code_hash: str) -> int:
        total = self.s.access_codes[code_hash] - self._by_code[code_hash]
        per_day = self.s.access_code_daily.get(code_hash, self.s.code_questions_per_day)
        return max(0, min(total, per_day - self._by_code_day[code_hash]))

    # ---- quota queries -----------------------------------------------
    def status(self, sid: str, ip: str, code_hash: str | None) -> dict:
        with self._lock:
            self._roll()
            public_left = max(0, min(
                self.s.public_questions_per_session - self._by_session[sid],
                self.s.public_questions_per_ip_per_day - self._by_ip[ip],
            ))
            locked_left = 0
            if code_hash and code_hash in self.s.access_codes:
                locked_left = self._code_left_locked(code_hash)
            return {
                "public_left": public_left,
                "public_limit": self.s.public_questions_per_session,
                "locked_left": locked_left,
                "unlocked": bool(code_hash and code_hash in self.s.access_codes),
                "service_paused": self._global_paused_locked(),
            }

    def _global_paused_locked(self) -> bool:
        return (self._global_q >= self.s.global_questions_per_day
                or self._global_usd >= self.s.global_spend_usd_per_day)

    def reserve(self, sid: str, ip: str, tier: str, code_hash: str | None) -> str | None:
        """Atomically check and consume one question. Returns an error code or None."""
        with self._lock:
            self._roll()
            if self._global_paused_locked():
                return "service_paused"
            if tier == "locked":
                if not code_hash or code_hash not in self.s.access_codes:
                    return "locked"
                if self._by_code[code_hash] >= self.s.access_codes[code_hash]:
                    return "code_exhausted"
                if self._code_left_locked(code_hash) <= 0:
                    return "code_daily"
                self._by_code[code_hash] += 1
                self._by_code_day[code_hash] += 1
            else:
                if self._by_session[sid] >= self.s.public_questions_per_session:
                    return "session_quota"
                if self._by_ip[ip] >= self.s.public_questions_per_ip_per_day:
                    return "ip_quota"
                self._by_session[sid] += 1
                self._by_ip[ip] += 1
            self._global_q += 1
            return None

    def refund(self, sid: str, ip: str, tier: str, code_hash: str | None) -> None:
        """Give the question back if it failed for reasons outside the user's control."""
        with self._lock:
            if tier == "locked" and code_hash:
                self._by_code[code_hash] = max(0, self._by_code[code_hash] - 1)
                self._by_code_day[code_hash] = max(0, self._by_code_day[code_hash] - 1)
            else:
                self._by_session[sid] = max(0, self._by_session[sid] - 1)
                self._by_ip[ip] = max(0, self._by_ip[ip] - 1)
            self._global_q = max(0, self._global_q - 1)

    def add_spend(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self._global_usd += max(0.0, float(usd or 0))


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def same_origin_ok() -> bool:
    """Reject cross-site POSTs (CSRF). Browsers always send Origin on POST."""
    origin = request.headers.get("Origin")
    if origin is None:
        return request.headers.get("X-Requested-With") == "fetch"
    return origin.rstrip("/") == request.host_url.rstrip("/")
