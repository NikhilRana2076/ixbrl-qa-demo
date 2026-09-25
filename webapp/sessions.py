"""
Per-visitor filing stores.

The original local demo kept ONE module-level STATE dict, so on a public
server every visitor would share (and overwrite) the same filing. Here each
browser session gets its own SQLite fact store in its own temporary folder.

Lifecycle / privacy:
  * The uploaded file is deleted as soon as it has been parsed; only the
    extracted fact store (a small .sqlite file) is kept.
  * The fact store is deleted when the visitor clicks "Clear filing", after
    SESSION_IDLE_MINUTES of inactivity, when the server restarts (Render's
    disk is ephemeral), or when the store is evicted to make room.

The app runs as ONE gunicorn worker with several threads, so this in-process
registry is shared by all requests. A lock guards it; each request opens its
own sqlite connection, so no connection is ever shared across threads.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FilingSession:
    sid: str
    workdir: Path
    db_path: Path
    filename: str
    overview: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


class FilingRegistry:
    def __init__(self, idle_minutes: int = 30, max_sessions: int = 12):
        self._items: dict[str, FilingSession] = {}
        self._lock = threading.Lock()
        self.idle_seconds = idle_minutes * 60
        self.max_sessions = max_sessions
        self.root = Path(tempfile.gettempdir()) / "ixbrl_sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def new_workdir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="s_", dir=self.root))

    def get(self, sid: str | None) -> FilingSession | None:
        if not sid:
            return None
        self.sweep()
        with self._lock:
            item = self._items.get(sid)
            if item:
                item.last_seen = time.time()
            return item

    def put(self, item: FilingSession) -> None:
        with self._lock:
            old = self._items.pop(item.sid, None)
            # Evict least-recently-used stores if at capacity.
            while len(self._items) >= self.max_sessions:
                lru = min(self._items.values(), key=lambda s: s.last_seen)
                self._drop_locked(lru.sid)
            self._items[item.sid] = item
        if old:
            shutil.rmtree(old.workdir, ignore_errors=True)

    def drop(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            self._drop_locked(sid)

    def _drop_locked(self, sid: str) -> None:
        item = self._items.pop(sid, None)
        if item:
            shutil.rmtree(item.workdir, ignore_errors=True)

    def sweep(self) -> None:
        cutoff = time.time() - self.idle_seconds
        with self._lock:
            for sid in [s for s, v in self._items.items() if v.last_seen < cutoff]:
                self._drop_locked(sid)

    def purge_all(self) -> None:
        with self._lock:
            for sid in list(self._items):
                self._drop_locked(sid)
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def __len__(self) -> int:
        return len(self._items)
