from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from klawde.transcript import PendingToolCache, find_pending_tool


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    with path.open("w") as f:
        for m in messages:
            f.write(json.dumps(m))
            f.write("\n")


def _assistant(content_blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "uuid": "a-" + str(time.time()),
        "message": {"role": "assistant", "content": content_blocks},
    }


def _user_with_results(tool_use_ids: list[str]) -> dict:
    return {
        "type": "user",
        "uuid": "u-" + str(time.time()),
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "ok"}
                for tid in tool_use_ids
            ],
        },
    }


def _tool_use(tid: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def test_empty_file_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert find_pending_tool(p) is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert find_pending_tool(tmp_path / "does-not-exist.jsonl") is None


def test_unresolved_tool_use_returns_pending(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _assistant([_tool_use("t1", "Bash", {"command": "rm -rf node_modules"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.name == "Bash"
    assert result.tool_use_id == "t1"
    assert result.input == {"command": "rm -rf node_modules"}


def test_resolved_tool_use_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _assistant([_tool_use("t1", "Bash", {"command": "ls"})]),
        _user_with_results(["t1"]),
    ])
    assert find_pending_tool(p) is None


def test_multiple_tools_only_last_unresolved(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _assistant([
            _tool_use("t1", "Read", {"file_path": "/a"}),
            _tool_use("t2", "Edit", {"file_path": "/b"}),
        ]),
        _user_with_results(["t1"]),
        _assistant([_tool_use("t3", "Bash", {"command": "sleep 1"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.name == "Bash"
    assert result.tool_use_id == "t3"


def test_multiple_tools_in_single_message_last_unresolved(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _assistant([
            _tool_use("t1", "Read", {"file_path": "/a"}),
            _tool_use("t2", "Edit", {"file_path": "/b"}),
        ]),
        _user_with_results(["t1"]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.tool_use_id == "t2"
    assert result.name == "Edit"


def test_garbage_lines_are_skipped(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    with p.open("w") as f:
        f.write("this is not json\n")
        f.write("{broken json\n")
        f.write(json.dumps(_assistant([_tool_use("t1", "Bash", {"command": "ok"})])) + "\n")
        f.write("\n")
        f.write("{}\n")
    result = find_pending_tool(p)
    assert result is not None
    assert result.name == "Bash"


def test_cache_no_refetch_on_same_mtime(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_assistant([_tool_use("t1", "Bash", {"command": "ok"})])])
    cache = PendingToolCache()
    first = cache.get("sid-1", p)
    assert first is not None

    calls = {"n": 0}
    import klawde.transcript as tmod

    orig = tmod.find_pending_tool

    def spy(path: Path):
        calls["n"] += 1
        return orig(path)

    monkeypatch.setattr(tmod, "find_pending_tool", spy)
    second = cache.get("sid-1", p)
    assert second == first
    assert calls["n"] == 0


def test_cache_refetches_on_mtime_change(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_assistant([_tool_use("t1", "Bash", {"command": "first"})])])
    cache = PendingToolCache()
    first = cache.get("sid-1", p)
    assert first is not None
    assert first.input == {"command": "first"}

    # Bump mtime deterministically by setting it explicitly in the future.
    stat = p.stat()
    os.utime(p, (stat.st_atime, stat.st_mtime + 2))
    # Overwrite content: resolve t1, add new pending tool.
    _write_jsonl(p, [
        _assistant([_tool_use("t1", "Bash", {"command": "first"})]),
        _user_with_results(["t1"]),
        _assistant([_tool_use("t2", "Edit", {"file_path": "/x"})]),
    ])
    # Re-set the future mtime after overwrite (write reset it).
    os.utime(p, (stat.st_atime, stat.st_mtime + 2))

    second = cache.get("sid-1", p)
    assert second is not None
    assert second.name == "Edit"


def test_cache_missing_file_returns_none_and_evicts(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_assistant([_tool_use("t1", "Bash", {"command": "ok"})])])
    cache = PendingToolCache()
    assert cache.get("sid-1", p) is not None
    p.unlink()
    assert cache.get("sid-1", p) is None


def _user_text(text: str) -> dict:
    return {
        "type": "user",
        "uuid": "u-" + str(time.time()),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _user_text_string_content(text: str) -> dict:
    return {
        "type": "user",
        "uuid": "u-" + str(time.time()),
        "message": {"role": "user", "content": text},
    }


def test_pending_tool_captures_preceding_user_prompt(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user_text("clean up npm deps please"),
        _assistant([_tool_use("t1", "Bash", {"command": "rm -rf node_modules"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.user_prompt == "clean up npm deps please"


def test_pending_tool_prompt_from_string_content(tmp_path: Path) -> None:
    # Some older transcript formats store content as a plain string.
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user_text_string_content("please delete node_modules"),
        _assistant([_tool_use("t1", "Bash", {"command": "rm -rf node_modules"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.user_prompt == "please delete node_modules"


def test_pending_tool_ignores_tool_result_only_user_messages(tmp_path: Path) -> None:
    # Intervening tool_result user messages must not overwrite the real prompt.
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user_text("investigate then fix"),
        _assistant([_tool_use("t0", "Read", {"file_path": "/etc/hosts"})]),
        _user_with_results(["t0"]),
        _assistant([_tool_use("t1", "Bash", {"command": "echo fixed"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.tool_use_id == "t1"
    assert result.user_prompt == "investigate then fix"


def test_pending_tool_no_prompt_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _assistant([_tool_use("t1", "Bash", {"command": "ls"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.user_prompt is None


def test_pending_tool_takes_latest_user_prompt(tmp_path: Path) -> None:
    # Two distinct user prompts; the more recent one wins.
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user_text("first ask"),
        _assistant([_tool_use("t0", "Read", {"file_path": "/a"})]),
        _user_with_results(["t0"]),
        _user_text("now do this other thing"),
        _assistant([_tool_use("t1", "Bash", {"command": "echo ok"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.user_prompt == "now do this other thing"


@pytest.mark.parametrize("spurious_type", ["attachment", "permission-mode", "file-history-snapshot"])
def test_ignores_non_user_non_assistant_types(tmp_path: Path, spurious_type: str) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": spurious_type, "uuid": "s-1", "content": "irrelevant"},
        _assistant([_tool_use("t1", "Bash", {"command": "ok"})]),
    ])
    result = find_pending_tool(p)
    assert result is not None
    assert result.name == "Bash"
