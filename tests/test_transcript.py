from __future__ import annotations

import json
from pathlib import Path

from klawde.tui import _read_transcript_meta, _transcript_path


def _assistant(model: str, usage: dict, ts: str, *, sidechain: bool = False, api_error: bool = False) -> dict:
    return {
        "parentUuid": "p",
        "isSidechain": sidechain,
        "isApiErrorMessage": api_error,
        "message": {
            "model": model,
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
        "timestamp": ts,
    }


def _user(text: str, ts: str) -> dict:
    return {
        "parentUuid": "p",
        "isSidechain": False,
        "message": {"role": "user", "content": text},
        "timestamp": ts,
    }


def _usage(ctx: int) -> dict:
    # Split across the three fields; sum equals ctx.
    return {
        "input_tokens": 1,
        "cache_read_input_tokens": ctx - 2,
        "cache_creation_input_tokens": 1,
        "output_tokens": 100,
    }


def test_picks_entry_with_latest_timestamp(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        fp.write(json.dumps(_user("hi", "2026-04-20T00:00:00Z")) + "\n")
        # File order: older entry FIRST, newer entry SECOND. Both main-chain.
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(100_000), "2026-04-20T00:00:01Z")) + "\n")
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(250_000), "2026-04-20T00:00:02Z")) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.context_tokens == 250_000


def test_picks_latest_timestamp_even_when_not_last_in_file(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        # Out-of-order: latest-timestamp entry written FIRST.
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(250_000), "2026-04-20T00:00:05Z")) + "\n")
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(100_000), "2026-04-20T00:00:01Z")) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.context_tokens == 250_000


def test_skips_sidechain_entries(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(50_000), "2026-04-20T00:00:01Z")) + "\n")
        # Sidechain with higher timestamp must NOT win.
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(900_000), "2026-04-20T00:00:02Z", sidechain=True)) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.context_tokens == 50_000


def test_skips_api_error_entries(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(50_000), "2026-04-20T00:00:01Z")) + "\n")
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(900_000), "2026-04-20T00:00:02Z", api_error=True)) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.context_tokens == 50_000


def test_skips_entries_without_timestamp(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        no_ts = _assistant("claude-opus-4-7", _usage(900_000), "")
        fp.write(json.dumps(no_ts) + "\n")
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(50_000), "2026-04-20T00:00:01Z")) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.context_tokens == 50_000


def test_captures_model_from_latest_entry(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        # /model switch mid-session — later entry uses the new model.
        fp.write(json.dumps(_assistant("claude-opus-4-7", _usage(100_000), "2026-04-20T00:00:01Z")) + "\n")
        fp.write(json.dumps(_assistant("claude-sonnet-4-6", _usage(110_000), "2026-04-20T00:00:02Z")) + "\n")

    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.model == "claude-sonnet-4-6"
    assert meta.context_tokens == 110_000


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert _read_transcript_meta(tmp_path / "nope.jsonl") is None


def test_non_claude_schema_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    f.write_text('{"random":"data"}\n{"also":"not claude"}\n')
    assert _read_transcript_meta(f) is None


def test_empty_file_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    f.write_text("")
    assert _read_transcript_meta(f) is None


def test_garbage_lines_ignored(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        fp.write("garbage line not json\n")
        fp.write('{"malformed": \n')
        fp.write(json.dumps(_assistant("claude-sonnet-4-6", _usage(35_000), "2026-04-20T00:00:01Z")) + "\n")
    meta = _read_transcript_meta(f)
    assert meta is not None
    assert meta.model == "claude-sonnet-4-6"
    assert meta.context_tokens == 35_000


def test_transcript_path_normalizes_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / ".claude" / "projects" / "-home-x"
    proj.mkdir(parents=True)
    (proj / "S.jsonl").write_text("{}\n")

    assert _transcript_path("/home/x", "S") == proj / "S.jsonl"
    assert _transcript_path("/home/x/", "S") == proj / "S.jsonl"
    assert _transcript_path("", "S") is None
    assert _transcript_path("/home/x", "") is None
    assert _transcript_path("/home/x", "MISSING") is None
