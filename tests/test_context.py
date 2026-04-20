from __future__ import annotations

from klawde.tui import _context_percent, _context_window, _ctx_bar


def test_opus_47_is_always_1m() -> None:
    assert _context_window("claude-opus-4-7") == 1_000_000
    assert _context_window("claude-opus-4-7[1m]") == 1_000_000  # [1m] would also match


def test_1m_suffix_is_version_agnostic() -> None:
    assert _context_window("claude-sonnet-4-6[1m]") == 1_000_000
    assert _context_window("claude-opus-4-6[1m]") == 1_000_000
    assert _context_window("claude-some-future-model[1m]") == 1_000_000


def test_sonnet_46_without_suffix_is_200k() -> None:
    # Pro users on base sonnet stay at 200k without explicit [1m] opt-in
    assert _context_window("claude-sonnet-4-6") == 200_000


def test_opus_46_without_suffix_is_200k() -> None:
    # Conservative: can't tell plan tier from model id alone
    assert _context_window("claude-opus-4-6") == 200_000


def test_other_models_are_200k() -> None:
    assert _context_window("claude-haiku-4-5") == 200_000
    assert _context_window("some-future-model") == 200_000
    assert _context_window(None) == 200_000
    assert _context_window("") == 200_000


def test_percent_clamps_and_rounds() -> None:
    assert _context_percent(0, 200_000) == 0
    assert _context_percent(100_000, 200_000) == 50
    assert _context_percent(190_000, 200_000) == 95
    assert _context_percent(200_000, 200_000) == 100
    assert _context_percent(250_000, 200_000) == 100
    assert _context_percent(200_000, 1_000_000) == 20
    assert _context_percent(1, 0) == 0


def test_ctx_bar_green_below_70() -> None:
    t = _ctx_bar(50)
    assert "green" in str(t.style)


def test_ctx_bar_yellow_70_to_85() -> None:
    assert "yellow" in str(_ctx_bar(70).style)
    assert "yellow" in str(_ctx_bar(84).style)


def test_ctx_bar_red_at_85_and_above() -> None:
    assert "red" in str(_ctx_bar(85).style)
    assert "red" in str(_ctx_bar(100).style)


def test_ctx_bar_warn_indicator_at_85() -> None:
    assert "⚠" not in _ctx_bar(84).plain
    assert "⚠" in _ctx_bar(85).plain
    assert "⚠" in _ctx_bar(100).plain


def test_ctx_bar_filled_matches_percent() -> None:
    # pct=50 with 10-wide bar → 5 filled
    assert _ctx_bar(50).plain.startswith("█████     ")
    # pct=30 → 3 filled
    assert _ctx_bar(30).plain.startswith("███       ")


def test_ctx_bar_zero_all_empty() -> None:
    assert _ctx_bar(0).plain == "           0%"


def test_ctx_bar_hundred_all_filled() -> None:
    assert _ctx_bar(100).plain == "██████████ 100% ⚠"


def test_ctx_bar_shows_percent_label() -> None:
    assert " 42%" in _ctx_bar(42).plain
