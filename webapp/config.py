"""
Runtime configuration, read from environment variables.

Every secret (API keys, SECRET_KEY, access codes) lives ONLY in the host's
environment settings (Render dashboard -> Environment). Nothing secret is
ever committed to the repository or sent to the browser.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def hash_code(code: str) -> str:
    """Access codes are case-insensitive and stored only as SHA-256 hashes."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def parse_access_codes(raw: str) -> dict[str, int]:
    """ACCESS_CODES="<sha256>:<quota>,<sha256>:<quota>"  (quota defaults to 15).

    Even someone who can read the environment cannot recover the plain codes.
    Generate entries with:  python -m webapp.make_code
    """
    codes: dict[str, int] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        digest, _, quota = item.partition(":")
        digest = digest.strip().lower()
        if len(digest) == 64:
            codes[digest] = int(quota) if quota.strip().isdigit() else 15
    return codes


def parse_code_daily(raw: str, default: int) -> dict[str, int]:
    """Optional third field per code: ACCESS_CODES="<sha256>:<quota>:<per-day>".

    Without it a code gets the default daily cap (CODE_QUESTIONS_PER_DAY), so a
    leaked or over-shared code can never burn through its whole allowance in one day.
    """
    daily: dict[str, int] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        parts = [x.strip() for x in item.split(":")]
        if len(parts[0]) == 64:
            digest = parts[0].lower()
            daily[digest] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else default
    return daily


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    secret_key: str = ""
    public_model: str = "gpt-5.6-terra"
    locked_model: str = "claude-sonnet-4-6"
    public_questions_per_session: int = 25
    public_questions_per_ip_per_day: int = 50
    global_questions_per_day: int = 300
    global_spend_usd_per_day: float = 2.0
    access_codes: dict = field(default_factory=dict)
    access_code_daily: dict = field(default_factory=dict)   # per-code questions per day
    code_questions_per_day: int = 15
    max_upload_mb: int = 25
    max_unzipped_mb: int = 120
    max_zip_members: int = 200
    max_concurrent_parses: int = 1
    session_idle_minutes: int = 30
    max_live_sessions: int = 12
    ask_per_minute: int = 6
    contact_email: str = ""
    contact_linkedin: str = ""
    samples_dir: str = "samples"
    site_url: str = "https://ixbrl.nikhilrana.com.np"
    redirect_from_hosts: tuple = ()          # old hostnames that 301 to site_url
    # Anonymous usage statistics (see webapp/stats.py)
    admin_token: str = ""                    # empty = admin page disabled
    database_url: str = ""                   # optional Postgres (e.g. Neon) so stats survive restarts
    stats_db_path: str = ""                  # SQLite fallback file
    stats_store_questions: bool = True       # keep question text + answer summary

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            env=e("APP_ENV", "development"),
            secret_key=e("SECRET_KEY", ""),
            public_model=e("PUBLIC_MODEL", "gpt-5.6-terra"),
            locked_model=e("LOCKED_MODEL", "claude-sonnet-4-6"),
            public_questions_per_session=_int("PUBLIC_QUESTIONS_PER_SESSION", 25),
            public_questions_per_ip_per_day=_int("PUBLIC_QUESTIONS_PER_IP_PER_DAY", 50),
            global_questions_per_day=_int("GLOBAL_QUESTIONS_PER_DAY", 300),
            global_spend_usd_per_day=_float("GLOBAL_SPEND_USD_PER_DAY", 2.0),
            access_codes=parse_access_codes(e("ACCESS_CODES", "")),
            access_code_daily=parse_code_daily(e("ACCESS_CODES", ""), _int("CODE_QUESTIONS_PER_DAY", 15)),
            code_questions_per_day=_int("CODE_QUESTIONS_PER_DAY", 15),
            max_upload_mb=_int("MAX_UPLOAD_MB", 25),
            max_unzipped_mb=_int("MAX_UNZIPPED_MB", 120),
            max_zip_members=_int("MAX_ZIP_MEMBERS", 200),
            max_concurrent_parses=_int("MAX_CONCURRENT_PARSES", 1),
            session_idle_minutes=_int("SESSION_IDLE_MINUTES", 30),
            max_live_sessions=_int("MAX_LIVE_SESSIONS", 12),
            ask_per_minute=_int("ASK_PER_MINUTE", 6),
            contact_email=e("CONTACT_EMAIL", ""),
            contact_linkedin=e("CONTACT_LINKEDIN", ""),
            samples_dir=e("SAMPLES_DIR", "samples"),
            site_url=e("SITE_URL", "https://ixbrl.nikhilrana.com.np").rstrip("/"),
            redirect_from_hosts=tuple(h.strip().lower() for h in e("REDIRECT_FROM_HOSTS", "").split(",")
                                      if h.strip()),
            admin_token=e("ADMIN_TOKEN", ""),
            database_url=e("DATABASE_URL", ""),
            stats_db_path=e("STATS_DB_PATH", ""),
            stats_store_questions=e("STATS_STORE_QUESTIONS", "1") not in ("0", "false", "no"),
        )
