from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Tail window — covers ~200 turns of a typical transcript. Large tool outputs
# are extracted to side files by Claude Code, so the JSONL itself stays bounded.
# Caveat: `user_prompt` is best-effort. A long-running session whose last user
# prompt predates the last 256 KB of transcript will render with prompt=None
# even though the file does contain one further back. Tool_use detection is
# unaffected — tool calls cluster near EOF by construction.
_TAIL_BYTES = 256 * 1024


@dataclass(frozen=True)
class PendingTool:
    name: str
    tool_use_id: str
    input: dict[str, Any]
    user_prompt: str | None = None


def _read_tail(path: Path) -> list[str]:
    """Read up to _TAIL_BYTES from EOF. Drop any leading partial line.
    Returns [] on any I/O error."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - _TAIL_BYTES)
            f.seek(start)
            blob = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    text = blob.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # If we didn't start at byte 0, the first line is (probably) a partial.
    if start > 0 and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln]


def _iter_tool_use_ids_in_message(msg: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Yield (id, name, input) for each tool_use content block in an assistant msg."""
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    out: list[tuple[str, str, dict[str, Any]]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tid = block.get("id")
        name = block.get("name")
        inp = block.get("input")
        if isinstance(tid, str) and isinstance(name, str):
            out.append((tid, name, inp if isinstance(inp, dict) else {}))
    return out


def _iter_tool_result_ids_in_message(msg: dict[str, Any]) -> list[str]:
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tid = block.get("tool_use_id")
        if isinstance(tid, str):
            out.append(tid)
    return out


def _extract_user_text(msg: dict[str, Any]) -> str | None:
    """Return the user-typed text from a user message. None if the message is
    a tool-result carrier (no real user input) or empty."""
    content = msg.get("content")
    if isinstance(content, str):
        s = content.strip()
        return s or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                txt = block.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt)
        if parts:
            return "\n".join(parts).strip()
    return None


def find_pending_tool(transcript_path: Path) -> PendingTool | None:
    """Parse transcript JSONL, return the last assistant tool_use block with
    no matching tool_result in a later user message. None on any absence /
    error. Never raises."""
    lines = _read_tail(transcript_path)
    if not lines:
        return None

    parsed: list[dict[str, Any]] = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            parsed.append(obj)

    # Pass 1: collect resolved tool_use_ids. Same pass records the index of
    # every user message with real text content — we'll use the latest one
    # that precedes the pending tool_use as the "what the user asked" context.
    resolved: set[str] = set()
    user_text_indices: list[int] = []
    for i, obj in enumerate(parsed):
        if obj.get("type") != "user":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        for tid in _iter_tool_result_ids_in_message(msg):
            resolved.add(tid)
        if _extract_user_text(msg) is not None:
            user_text_indices.append(i)

    # Pass 2: scan assistant messages in reverse, return the last unresolved
    # tool_use. Within a single message, later blocks win.
    for idx in range(len(parsed) - 1, -1, -1):
        obj = parsed[idx]
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        uses = _iter_tool_use_ids_in_message(msg)
        for tid, name, inp in reversed(uses):
            if tid in resolved:
                continue
            # Find the last user-text message at index < idx.
            prompt: str | None = None
            for ui in reversed(user_text_indices):
                if ui < idx:
                    umsg = parsed[ui].get("message")
                    if isinstance(umsg, dict):
                        prompt = _extract_user_text(umsg)
                    break
            return PendingTool(
                name=name, tool_use_id=tid, input=inp, user_prompt=prompt
            )

    return None


class PendingToolCache:
    """mtime-keyed cache. Re-parses only when the transcript has grown/changed."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, PendingTool | None]] = {}

    def get(self, session_id: str, transcript_path: Path) -> PendingTool | None:
        try:
            mtime = transcript_path.stat().st_mtime
        except (FileNotFoundError, PermissionError, OSError):
            self._cache.pop(session_id, None)
            return None
        cached = self._cache.get(session_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        result = find_pending_tool(transcript_path)
        self._cache[session_id] = (mtime, result)
        return result
