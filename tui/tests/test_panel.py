from __future__ import annotations

from datetime import UTC, datetime, timedelta

from klawde.db import RateLimits
from klawde.tui import (
    _fmt_burn_cell,
    _fmt_idle,
    _fmt_pct_cell,
    _fmt_resets_cell,
    _summary_row,
)


def test_fmt_idle_seconds() -> None:
    t = datetime.now(UTC) - timedelta(seconds=12)
    assert _fmt_idle(t) == "12s"


def test_fmt_idle_minutes() -> None:
    t = datetime.now(UTC) - timedelta(minutes=3, seconds=5)
    assert _fmt_idle(t) == "3m"


def test_fmt_idle_hours() -> None:
    t = datetime.now(UTC) - timedelta(hours=2, minutes=30)
    assert _fmt_idle(t) == "2h30m"


def test_fmt_idle_clock_skew_future() -> None:
    t = datetime.now(UTC) + timedelta(seconds=2)
    assert _fmt_idle(t) == "0s"


def test_fmt_pct_cell() -> None:
    assert _fmt_pct_cell(None) == "—"
    assert _fmt_pct_cell(45.0) == "45%"
    assert _fmt_pct_cell(45.6) == "46%"


def test_fmt_resets_cell_none() -> None:
    assert _fmt_resets_cell(None, include_date=False) == "—"
    assert _fmt_resets_cell(None, include_date=True) == "—"


def test_fmt_burn_cell_none_and_zero() -> None:
    assert _fmt_burn_cell(None) == "—"
    assert _fmt_burn_cell(0.0) == "—"
    assert _fmt_burn_cell(-1.0) == "—"


def test_fmt_burn_cell_small() -> None:
    assert _fmt_burn_cell(12.345) == "$12.35/hr"


def test_fmt_burn_cell_large() -> None:
    assert _fmt_burn_cell(1234.5) == "$1,234/hr"


def test_summary_row_all_dashes_when_empty() -> None:
    row = _summary_row(None, None)
    assert row == ("⏳ —", "🔄 —", "📅 —", "🔄 —", "🔥 —")


def test_summary_row_populates_cells() -> None:
    rl = RateLimits(
        five_hour_pct=45.0, five_hour_resets_at=None,
        seven_day_pct=12.0, seven_day_resets_at=None,
    )
    row = _summary_row(rl, 18.0)
    assert row == ("⏳ 45%", "🔄 —", "📅 12%", "🔄 —", "🔥 $18.00/hr")
