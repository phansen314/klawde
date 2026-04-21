from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Session:
    session_id: str
    cwd: str
    status: str
    model: str | None
    context_percent: int | None
    started_at: datetime
    kitty_window_id: int | None


_SQL = """
SELECT
  s.session_id,
  s.cwd,
  s.status,
  s.model,
  s.context_percent,
  s.started_at,
  MAX(CASE WHEN m.key = 'window_id' THEN m.value END) AS kitty_window_id
FROM sessions s
LEFT JOIN session_metadata m
  ON s.session_id = m.session_id AND m.namespace = 'kitty'
WHERE s.status IN ('running', 'needs_approval')
GROUP BY s.session_id
ORDER BY (s.status = 'needs_approval') DESC, s.started_at DESC
"""


def _default_db_path() -> Path:
    env = os.environ.get("KLAWDE_DB")
    if env:
        return Path(env)
    return Path.home() / ".klawde" / "sessions.db"


def _parse_ts(s: str) -> datetime:
    # started_at is ISO-8601 UTC from metrics/session_start.sh's now() helper.
    # Accept both "Z" and "+00:00" suffixes defensively.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _row_to_session(r: sqlite3.Row) -> Session:
    kw = r["kitty_window_id"]
    try:
        kitty = int(kw) if kw is not None and str(kw).strip() != "" else None
    except (TypeError, ValueError):
        kitty = None
    return Session(
        session_id=r["session_id"],
        cwd=r["cwd"] or "",
        status=r["status"],
        model=r["model"],
        context_percent=r["context_percent"],
        started_at=_parse_ts(r["started_at"]),
        kitty_window_id=kitty,
    )


class SessionRepo:
    """Read-only accessor for the klawde metrics DB. Opens one connection
    (WAL means readers don't block writers) and retries transparently on
    transient errors by returning an empty list from list_sessions.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=2.0,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def list_sessions(self) -> list[Session]:
        try:
            conn = self._connect()
            rows = conn.execute(_SQL).fetchall()
        except sqlite3.Error:
            # DB missing, locked, or mid-WAL-checkpoint. Next tick retries.
            self.close()
            return []
        except ValueError:
            # started_at parse failure on a malformed row. Swallow; still
            # better than crashing the TUI.
            return []
        return [_row_to_session(r) for r in rows]

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
