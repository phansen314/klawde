from __future__ import annotations

from klawde.tui import _fmt_pending_input, _truncate


def test_bash_command_rendered_plainly() -> None:
    out = _fmt_pending_input(
        "Bash", {"command": "rm -rf node_modules", "description": "Clean deps"}
    ).plain
    assert "command:" in out
    assert "  rm -rf node_modules" in out
    assert "description:" in out
    assert "  Clean deps" in out


def test_bash_field_order() -> None:
    out = _fmt_pending_input(
        "Bash", {"description": "x", "command": "ls"}
    ).plain
    # command should appear before description regardless of dict order
    assert out.index("command:") < out.index("description:")


def test_edit_shows_old_and_new_strings() -> None:
    out = _fmt_pending_input(
        "Edit",
        {"file_path": "/tmp/a.py", "old_string": "foo", "new_string": "bar"},
    ).plain
    assert "file_path:\n  /tmp/a.py" in out
    assert "old_string:\n  foo" in out
    assert "new_string:\n  bar" in out


def test_unknown_tool_falls_back_to_dict_iteration() -> None:
    out = _fmt_pending_input("SomeNewTool", {"foo": "bar", "baz": 42}).plain
    assert "foo:\n  bar" in out
    assert "baz:\n  42" in out


def test_empty_values_are_skipped() -> None:
    out = _fmt_pending_input("Bash", {"command": "ls", "description": ""}).plain
    assert "command:" in out
    assert "description:" not in out


def test_booleans_render_as_lowercase_strings() -> None:
    out = _fmt_pending_input(
        "Edit",
        {"file_path": "/a", "old_string": "x", "new_string": "y", "replace_all": True},
    ).plain
    assert "replace_all:\n  true" in out


def test_truncate_caps_long_strings() -> None:
    raw = "\n".join(f"line{i}" for i in range(100))
    truncated = _truncate(raw, max_lines=5)
    assert truncated.startswith("line0\nline1\nline2\nline3\nline4\n")
    assert "(95 more lines)" in truncated


def test_extra_keys_appended_after_known_fields() -> None:
    # A future Bash input adds a new field we don't know about — must not be dropped.
    out = _fmt_pending_input(
        "Bash", {"command": "ls", "future_field": "keep-me"}
    ).plain
    assert "command:" in out
    assert "future_field:\n  keep-me" in out
    assert out.index("command:") < out.index("future_field:")


def test_nested_dict_coerced_to_json() -> None:
    out = _fmt_pending_input("Task", {"todos": [{"subject": "x", "status": "pending"}]}).plain
    assert "todos:" in out
    assert "subject" in out
    assert "pending" in out


def test_askuserquestion_single_question() -> None:
    inp = {
        "questions": [{
            "question": "Which fib(n) impl?",
            "header": "Fib impl",
            "multiSelect": False,
            "options": [
                {"label": "Iterative", "description": "O(n) time, O(1) space.",
                 "preview": "def fib(n): ..."},
                {"label": "Recursion", "description": "O(2^n). Teaching only."},
                {"label": "Memoized"},
            ],
        }],
    }
    out = _fmt_pending_input("AskUserQuestion", inp).plain
    assert "Which fib(n) impl?" in out
    assert "[1] Iterative — O(n) time, O(1) space." in out
    assert "[2] Recursion — O(2^n). Teaching only." in out
    assert "[3] Memoized" in out
    # preview content must NOT be dumped into the modal
    assert "def fib(n)" not in out
    # single question → no Q1: prefix
    assert "Q1:" not in out


def test_askuserquestion_multi_question_prefixes() -> None:
    inp = {
        "questions": [
            {"question": "First?",  "options": [{"label": "A"}, {"label": "B"}]},
            {"question": "Second?", "options": [{"label": "C"}]},
        ],
    }
    out = _fmt_pending_input("AskUserQuestion", inp).plain
    assert "Q1: First?" in out
    assert "Q2: Second?" in out
    assert "[1] A" in out
    assert "[2] B" in out
    assert out.index("Q1:") < out.index("Q2:")


def test_todowrite_renders_bullets() -> None:
    inp = {
        "todos": [
            {"subject": "build parser", "status": "in_progress"},
            {"subject": "write tests",  "status": "pending"},
            {"content": "legacy schema", "status": "completed"},
        ],
    }
    out = _fmt_pending_input("TodoWrite", inp).plain
    assert "• [in_progress] build parser" in out
    assert "• [pending] write tests" in out
    assert "• [completed] legacy schema" in out


def test_todowrite_handles_missing_status() -> None:
    out = _fmt_pending_input("TodoWrite", {"todos": [{"subject": "no status"}]}).plain
    assert "• no status" in out
    assert "[]" not in out
