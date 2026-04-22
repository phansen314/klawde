from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STATUS_RUNNING = "running"
STATUS_NEEDS_APPROVAL = "needs_approval"
STATUS_STOPPED = "stopped"
LIVE_STATUSES = (STATUS_RUNNING, STATUS_NEEDS_APPROVAL)


@dataclass
class Session:
    session_id: str
    cwd: str
    status: str
    model: str | None
    context_percent: int | None
    started_at: datetime
    kitty_window_id: int | None
    total_cost_usd: float | None
    updated_at: datetime
    transcript_path: str | None


@dataclass
class RateLimits:
    five_hour_pct: float | None
    five_hour_resets_at: datetime | None
    seven_day_pct: float | None
    seven_day_resets_at: datetime | None


_SQL = """
SELECT
  s.session_id,
  s.cwd,
  s.status,
  s.model,
  s.context_percent,
  s.started_at,
  s.total_cost_usd,
  s.updated_at,
  s.transcript_path,
  MAX(CASE WHEN m.key = 'window_id' THEN m.value END) AS kitty_window_id
FROM sessions s
LEFT JOIN session_metadata m
  ON s.session_id = m.session_id AND m.namespace = 'kitty'
WHERE s.status IN ('running', 'needs_approval')
GROUP BY s.session_id
ORDER BY (s.status = 'needs_approval') DESC, s.started_at DESC
"""


_BURN_SQL = """
SELECT
  SUM(
    (s.total_cost_usd - s.prev_cost_usd) * 3600.0
    / NULLIF(strftime('%s','now') - strftime('%s', s.prev_cost_sampled_at), 0)
  ) AS burn_usd_per_hr
FROM sessions s
WHERE s.status IN ('running', 'needs_approval')
  AND s.total_cost_usd IS NOT NULL
  AND s.prev_cost_usd IS NOT NULL
  AND s.prev_cost_sampled_at IS NOT NULL
"""


_RL_SQL = """
SELECT
  rate_limit_5h_percent,
  rate_limit_5h_resets_at,
  rate_limit_7d_percent,
  rate_limit_7d_resets_at
FROM sessions
WHERE status IN ('running', 'needs_approval')
  AND (rate_limit_5h_percent IS NOT NULL
    OR rate_limit_7d_percent IS NOT NULL)
ORDER BY updated_at DESC
LIMIT 1
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
        total_cost_usd=r["total_cost_usd"],
        updated_at=_parse_ts(r["updated_at"]),
        transcript_path=r["transcript_path"],
    )


def _parse_ts_or_none(s: str | None) -> datetime | None:
    if s is None or s == "":
        return None
    try:
        return _parse_ts(s)
    except ValueError:
        return None


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

    def get_burn_rate_per_hr(self) -> float | None:
        try:
            conn = self._connect()
            row = conn.execute(_BURN_SQL).fetchone()
        except sqlite3.Error:
            self.close()
            return None
        if row is None:
            return None
        val = row["burn_usd_per_hr"]
        if val is None or val <= 0:  # SUM returns NULL when no rows qualify
            return None
        return float(val)

    def get_rate_limits(self) -> RateLimits | None:
        try:
            conn = self._connect()
            row = conn.execute(_RL_SQL).fetchone()
        except sqlite3.Error:
            self.close()
            return None
        if row is None:
            return None
        return RateLimits(
            five_hour_pct=row["rate_limit_5h_percent"],
            five_hour_resets_at=_parse_ts_or_none(row["rate_limit_5h_resets_at"]),
            seven_day_pct=row["rate_limit_7d_percent"],
            seven_day_resets_at=_parse_ts_or_none(row["rate_limit_7d_resets_at"]),
        )

    def reset_needs_approval(self, session_id: str) -> bool:
        """Transition status 'needs_approval' → 'running'. Returns True iff a
        row was changed. Opens a short-lived RW connection (mode=rw — fails
        instead of creating if the DB is missing). Also unlinks the approval
        flag file notification.sh creates."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=rw", uri=True, timeout=2.0
            )
            try:
                cur = conn.execute(
                    "UPDATE sessions SET status=?, updated_at=? "
                    "WHERE session_id=? AND status=?",
                    (STATUS_RUNNING, now, session_id, STATUS_NEEDS_APPROVAL),
                )
                conn.commit()
                changed = cur.rowcount > 0
            finally:
                conn.close()
        except sqlite3.Error:
            return False
        if changed:
            flag_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
            try:
                Path(flag_dir, f"klawde-needs-approval-{session_id}").unlink()
            except (FileNotFoundError, OSError):
                pass
        return changed

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
