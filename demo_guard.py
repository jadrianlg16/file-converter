"""Demo-instance guard — opt-in usage limits for hosting a public demo.

The full self-hosted app has no limits; this module only activates when the
``DEMO_MODE=1`` env var is set (e.g. on the public demo at adriangaona.dev),
bounding worst-case abuse of a shared box:

  DEMO_MODE            "1" enables the guard (default: off)
  DEMO_MAX_UPLOAD_MB   per-file upload cap (default 10; full app: MAX_UPLOAD_MB)
  DEMO_RATE_PER_HOUR   conversions per client IP per rolling hour (default 5)
  DEMO_DAILY_BUDGET    global conversions per UTC day, all visitors (default 200)
  DEMO_BLOCK           comma-separated extensions to refuse entirely (default "")
  DEMO_REPO_URL        self-host link shown in limit messages
  DEMO_PROXY_HOPS      reverse proxies in front of the app that append to
                       X-Forwarded-For (default 1; 0 = exposed directly)

Counters live in a small SQLite database in DATA_DIR so they are shared
across gunicorn workers and survive restarts. Stdlib-only on purpose — the
module is unit-testable without Flask or any conversion engine installed.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

_HOUR = 3600


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    status: int = 200
    error: str = ""


class DemoGuard:
    def __init__(self, db_path: str):
        self.enabled = os.environ.get("DEMO_MODE", "") == "1"
        self.max_upload_mb = _env_int("DEMO_MAX_UPLOAD_MB", 10)
        self.rate_per_hour = _env_int("DEMO_RATE_PER_HOUR", 5)
        self.daily_budget = _env_int("DEMO_DAILY_BUDGET", 200)
        self.blocked = {
            e.strip().lstrip(".").lower()
            for e in os.environ.get("DEMO_BLOCK", "").split(",")
            if e.strip()
        }
        self.repo_url = os.environ.get("DEMO_REPO_URL", "https://github.com/")
        self.proxy_hops = max(0, _env_int("DEMO_PROXY_HOPS", 1))
        self.db_path = db_path
        if self.enabled:
            self._init_db()

    # -- public API ----------------------------------------------------------

    def check_and_count(self, ip: str, src: str, target: str,
                        now: float | None = None) -> Verdict:
        """Gate one conversion attempt. Counts the attempt when allowed."""
        if not self.enabled:
            return Verdict(True)
        now = time.time() if now is None else now

        if src in self.blocked or target in self.blocked:
            return Verdict(
                False, 403,
                f".{src} → .{target} is disabled on this demo instance. "
                f"The self-hosted app supports it: {self.repo_url}",
            )

        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        # closing() is required: sqlite3's context manager commits but does
        # not close, which leaks handles (and locks the file on Windows).
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM hits WHERE ts < ?", (now - _HOUR,))
            (used_today,) = db.execute(
                "SELECT COALESCE((SELECT n FROM budget WHERE day = ?), 0)", (day,)
            ).fetchone()
            if used_today >= self.daily_budget:
                return Verdict(
                    False, 429,
                    "Today's demo budget is spent. It resets at midnight UTC — "
                    f"or run the full app yourself, no limits: {self.repo_url}",
                )
            (recent,) = db.execute(
                "SELECT COUNT(*) FROM hits WHERE ip = ? AND ts >= ?",
                (ip, now - _HOUR),
            ).fetchone()
            if recent >= self.rate_per_hour:
                return Verdict(
                    False, 429,
                    f"Demo limit: {self.rate_per_hour} conversions per hour. "
                    f"Self-host for unlimited use: {self.repo_url}",
                )
            db.execute("INSERT INTO hits (ip, ts) VALUES (?, ?)", (ip, now))
            db.execute(
                "INSERT INTO budget (day, n) VALUES (?, 1) "
                "ON CONFLICT(day) DO UPDATE SET n = n + 1",
                (day,),
            )
        return Verdict(True)

    def public_info(self) -> dict | None:
        """Demo facts for the UI banner; None when the guard is off."""
        if not self.enabled:
            return None
        return {
            "maxUploadMb": self.max_upload_mb,
            "ratePerHour": self.rate_per_hour,
            "repoUrl": self.repo_url,
        }

    # -- internals -----------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS hits (ip TEXT NOT NULL, ts REAL NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS hits_ip_ts ON hits (ip, ts)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY, n INTEGER NOT NULL)"
            )


def client_ip(headers, remote_addr: str | None, proxy_hops: int = 1) -> str:
    """Real client IP when running behind ``proxy_hops`` reverse proxies.

    Each proxy *appends* the peer it saw to X-Forwarded-For, so only the last
    ``proxy_hops`` entries are trustworthy — everything to their left came
    from the client and can be forged (taking the leftmost entry would let
    anyone dodge the per-IP limit with a fake header). With ``proxy_hops=0``
    the header is ignored and the socket peer is used.
    """
    if proxy_hops > 0:
        chain = [p.strip() for p in headers.get("X-Forwarded-For", "").split(",")
                 if p.strip()]
        if chain:
            return chain[-min(proxy_hops, len(chain))]
    return remote_addr or "unknown"
