# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Tests for spawn_task_gate — fail-open coverage + happy paths + first-turn semantics."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spawn_task_gate as gate


# ---- is_gate_armed --------------------------------------------------------


def test_is_gate_armed_false_when_flag_absent(tmp_path):
    assert gate.is_gate_armed(state_dir=tmp_path) is False


def test_is_gate_armed_true_when_flag_present(tmp_path):
    (tmp_path / gate.GATE_FLAG_FILENAME).touch()
    assert gate.is_gate_armed(state_dir=tmp_path) is True


def test_is_gate_armed_false_on_exception(monkeypatch, tmp_path):
    def boom(self):
        raise OSError("simulated")
    monkeypatch.setattr(Path, "exists", boom)
    assert gate.is_gate_armed(state_dir=tmp_path) is False


# ---- is_chip_spawned_session ---------------------------------------------


def test_is_chip_spawned_false_when_sid_empty(tmp_path):
    assert gate.is_chip_spawned_session("", state_dir=tmp_path) is False


def test_is_chip_spawned_false_when_marker_missing(tmp_path):
    assert gate.is_chip_spawned_session("sid1", state_dir=tmp_path) is False


def test_is_chip_spawned_true_when_marker_valid(tmp_path):
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text(json.dumps({"origin": "chip"}))
    assert gate.is_chip_spawned_session("sid1", state_dir=tmp_path) is True


def test_is_chip_spawned_false_on_bad_origin(tmp_path):
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text(json.dumps({"origin": "other"}))
    assert gate.is_chip_spawned_session("sid1", state_dir=tmp_path) is False


def test_is_chip_spawned_false_on_malformed_json(tmp_path):
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text("not json{")
    assert gate.is_chip_spawned_session("sid1", state_dir=tmp_path) is False


def test_is_chip_spawned_false_on_non_dict(tmp_path):
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text(json.dumps(["chip"]))
    assert gate.is_chip_spawned_session("sid1", state_dir=tmp_path) is False


# ---- extract_task_id ------------------------------------------------------


def test_extract_task_id_valid():
    text = "Noted (position 1, task_id: task_bf5d1978). A chip is showing..."
    assert gate.extract_task_id(text) == "task_bf5d1978"


def test_extract_task_id_no_match():
    assert gate.extract_task_id("no id here") is None


def test_extract_task_id_non_string():
    assert gate.extract_task_id(None) is None
    assert gate.extract_task_id(123) is None


# ---- append_chip_spawn + load_recent_chip_spawns --------------------------


def test_append_and_load_roundtrip(tmp_path):
    now = 1_000_000.0
    assert gate.append_chip_spawn(
        task_id="task_a", parent_session_id="parent1", prompt="hello",
        state_dir=tmp_path, now=now,
    )
    entries = gate.load_recent_chip_spawns(state_dir=tmp_path, now=now + 60)
    assert len(entries) == 1
    assert entries[0]["task_id"] == "task_a"
    assert entries[0]["parent_session_id"] == "parent1"
    assert entries[0]["prompt_sha256"] == gate.prompt_sha256("hello")


def test_append_prunes_old_entries(tmp_path):
    old = 1_000_000.0
    new = old + gate.CHIP_SPAWNS_RETENTION_SECONDS + 3600
    gate.append_chip_spawn(task_id="old_task", parent_session_id="p", prompt="x",
                           state_dir=tmp_path, now=old)
    gate.append_chip_spawn(task_id="new_task", parent_session_id="p", prompt="y",
                           state_dir=tmp_path, now=new)
    entries = gate.load_recent_chip_spawns(state_dir=tmp_path, now=new + 60)
    assert [e["task_id"] for e in entries] == ["new_task"]


def test_append_rejects_missing_inputs(tmp_path):
    assert not gate.append_chip_spawn(task_id="", parent_session_id="p", prompt="x",
                                       state_dir=tmp_path)
    assert not gate.append_chip_spawn(task_id="t", parent_session_id="", prompt="x",
                                       state_dir=tmp_path)


def test_load_recent_returns_empty_on_missing_file(tmp_path):
    assert gate.load_recent_chip_spawns(state_dir=tmp_path) == []


def test_load_recent_skips_malformed_rows(tmp_path):
    now = 1_000_000.0
    path = tmp_path / gate.CHIP_SPAWNS_FILENAME
    path.write_text(
        "not json\n"
        + json.dumps({"task_id": "t", "spawned_at": now}) + "\n"
        + "{}\n"  # missing spawned_at
    )
    entries = gate.load_recent_chip_spawns(state_dir=tmp_path, now=now + 60)
    assert len(entries) == 1
    assert entries[0]["task_id"] == "t"


# ---- maybe_write_session_origin ------------------------------------------


def test_write_origin_on_prompt_match(tmp_path):
    now = 1_000_000.0
    gate.append_chip_spawn(task_id="task_x", parent_session_id="parent",
                           prompt="hello world", state_dir=tmp_path, now=now)
    result = gate.maybe_write_session_origin(
        sid="child_sid", first_user_prompt="hello world",
        state_dir=tmp_path, now=now + 30,
    )
    assert result == "task_x"
    assert gate.is_chip_spawned_session("child_sid", state_dir=tmp_path) is True


def test_write_origin_no_match(tmp_path):
    gate.append_chip_spawn(task_id="task_x", parent_session_id="parent",
                           prompt="hello world", state_dir=tmp_path, now=1e6)
    result = gate.maybe_write_session_origin(
        sid="child_sid", first_user_prompt="different prompt",
        state_dir=tmp_path, now=1e6 + 30,
    )
    assert result is None
    assert gate.is_chip_spawned_session("child_sid", state_dir=tmp_path) is False


def test_write_origin_first_turn_sentinel(tmp_path):
    """Second call for the same sid is a no-op even with matching prompt."""
    gate.append_chip_spawn(task_id="task_x", parent_session_id="parent",
                           prompt="hello", state_dir=tmp_path, now=1e6)
    assert gate.maybe_write_session_origin(
        sid="s", first_user_prompt="hello", state_dir=tmp_path, now=1e6 + 30
    ) == "task_x"
    # sentinel now exists; second call does nothing
    assert gate.maybe_write_session_origin(
        sid="s", first_user_prompt="hello", state_dir=tmp_path, now=1e6 + 60
    ) is None


def test_write_origin_rejects_empty_sid(tmp_path):
    assert gate.maybe_write_session_origin(
        sid="", first_user_prompt="hello", state_dir=tmp_path,
    ) is None


# ---- pretool_deny_or_none -------------------------------------------------


def _arm_chip_session(tmp_path, sid="sid1"):
    """Helper: create the flag + a chip-origin marker for `sid`."""
    (tmp_path / gate.GATE_FLAG_FILENAME).touch()
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir(exist_ok=True)
    (origins / f"{sid}.json").write_text(json.dumps({"origin": "chip"}))


def _noop_notify(title, message):
    """Silent notify_fn — every deny-path test injects this so pytest doesn't
    pop real macOS notifications through /usr/bin/osascript."""
    return None


def test_pretool_deny_when_all_conjuncts_hold(tmp_path):
    _arm_chip_session(tmp_path)
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        notify_fn=_noop_notify,
    )
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "defer_to_backlog.py" in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cannot override" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_pretool_deny_none_when_flag_dormant(tmp_path):
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text(json.dumps({"origin": "chip"}))
    # flag NOT present
    assert gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
    ) is None


def test_pretool_deny_none_when_not_chip_spawned(tmp_path):
    (tmp_path / gate.GATE_FLAG_FILENAME).touch()
    # no session-origins marker
    assert gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
    ) is None


def test_pretool_deny_none_for_other_tool(tmp_path):
    _arm_chip_session(tmp_path)
    assert gate.pretool_deny_or_none(
        tool_name="Bash", session_id="sid1", state_dir=tmp_path,
    ) is None


def test_pretool_deny_none_on_helper_exception(monkeypatch, tmp_path):
    _arm_chip_session(tmp_path)
    def boom(*a, **kw):
        raise RuntimeError("simulated")
    monkeypatch.setattr(gate, "is_chip_spawned_session", boom)
    # outer try/except catches → None → tool call ALLOWED (fail-open)
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
    )
    assert result is None


def test_pretool_deny_none_on_render_exception(monkeypatch, tmp_path):
    _arm_chip_session(tmp_path)
    def boom(*a, **kw):
        raise RuntimeError("simulated")
    monkeypatch.setattr(gate, "render_block_message", boom)
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        notify_fn=_noop_notify,
    )
    assert result is None


# ---- extract_bypass_reason ------------------------------------------------


def test_extract_bypass_reason_none_on_non_dict():
    assert gate.extract_bypass_reason(None) is None
    assert gate.extract_bypass_reason("prompt-string") is None
    assert gate.extract_bypass_reason(["list"]) is None


def test_extract_bypass_reason_none_on_missing_token():
    assert gate.extract_bypass_reason({"prompt": "just a normal chip prompt"}) is None


def test_extract_bypass_reason_from_prompt():
    ti = {"prompt": "spawn this because it's urgent\n# spawn-task-bypass: operator green-lit this"}
    assert gate.extract_bypass_reason(ti) == "operator green-lit this"


def test_extract_bypass_reason_from_title():
    ti = {"title": "fix bug # spawn-task-bypass: needs decision"}
    assert gate.extract_bypass_reason(ti) == "needs decision"


def test_extract_bypass_reason_from_tldr():
    ti = {"tldr": "# spawn-task-bypass: routine cleanup approved"}
    assert gate.extract_bypass_reason(ti) == "routine cleanup approved"


def test_extract_bypass_reason_stops_at_newline():
    ti = {"prompt": "# spawn-task-bypass: first reason\nother content # spawn-task-bypass: second"}
    # Regex captures up to newline; first match wins.
    assert gate.extract_bypass_reason(ti) == "first reason"


def test_extract_bypass_reason_strips_whitespace():
    ti = {"prompt": "# spawn-task-bypass:    lots of leading space   "}
    assert gate.extract_bypass_reason(ti) == "lots of leading space"


def test_extract_bypass_reason_none_on_empty_reason():
    ti = {"prompt": "# spawn-task-bypass:   "}
    # Empty reason (only whitespace) → None, so a bare marker doesn't accidentally
    # authorize the bypass.
    assert gate.extract_bypass_reason(ti) is None


def test_extract_bypass_reason_ignores_non_string_fields():
    ti = {"prompt": None, "title": 42, "tldr": {"nested": "dict"}}
    assert gate.extract_bypass_reason(ti) is None


# ---- notify path ----------------------------------------------------------


def test_default_notify_writes_to_stub(tmp_path, monkeypatch):
    stub = tmp_path / "notify-stub.log"
    monkeypatch.setenv(gate.NOTIFY_STUB_ENV, str(stub))
    gate._default_notify("Test Title", "Test Message")
    assert stub.exists()
    assert stub.read_text() == "Test Title|Test Message\n"


def test_default_notify_appends_multiple(tmp_path, monkeypatch):
    stub = tmp_path / "notify-stub.log"
    monkeypatch.setenv(gate.NOTIFY_STUB_ENV, str(stub))
    gate._default_notify("A", "1")
    gate._default_notify("B", "2")
    lines = stub.read_text().splitlines()
    assert lines == ["A|1", "B|2"]


def test_default_notify_fail_open_on_stub_error(tmp_path, monkeypatch):
    # Stub path is a directory → open('a') raises IsADirectoryError; must
    # not propagate. Notification is advisory, never load-bearing.
    stub_dir = tmp_path / "not-a-file"
    stub_dir.mkdir()
    monkeypatch.setenv(gate.NOTIFY_STUB_ENV, str(stub_dir))
    gate._default_notify("Test", "Test")  # should NOT raise


def test_pretool_deny_fires_block_notify(tmp_path):
    sid = "abc12345-6789-def0-1234-56789abcdef0"  # UUID-shape sid
    _arm_chip_session(tmp_path, sid=sid)
    calls = []
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id=sid, state_dir=tmp_path,
        notify_fn=lambda t, m: calls.append((t, m)),
    )
    assert result is not None  # deny fired
    assert len(calls) == 1
    title, message = calls[0]
    assert "blocked" in title.lower()
    assert sid[:8] in message  # first 8 chars of sid
    assert "cannot be overridden" in message


def test_pretool_deny_notify_failure_never_breaks_deny(tmp_path):
    _arm_chip_session(tmp_path)
    def boom(*a, **kw):
        raise RuntimeError("notification service crashed")
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        notify_fn=boom,
    )
    # Deny still fires even though notify raised.
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- bypass path ----------------------------------------------------------


def test_pretool_bypass_token_in_prompt_does_not_bypass_gate(tmp_path):
    _arm_chip_session(tmp_path)
    calls = []
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        tool_input={"prompt": "Ship this chip\n# spawn-task-bypass: operator approved"},
        notify_fn=lambda t, m: calls.append((t, m)),
    )
    assert result is not None
    assert len(calls) == 1
    title, message = calls[0]
    assert "blocked" in title.lower()
    assert "cannot be overridden" in message


def test_pretool_bypass_never_fires_when_flag_dormant(tmp_path):
    # Flag NOT armed → returns None BEFORE the bypass check runs.
    origins = tmp_path / gate.SESSION_ORIGINS_DIRNAME
    origins.mkdir()
    (origins / "sid1.json").write_text(json.dumps({"origin": "chip"}))
    calls = []
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        tool_input={"prompt": "# spawn-task-bypass: test"},
        notify_fn=lambda t, m: calls.append((t, m)),
    )
    assert result is None
    # Dormant BLOCK never fires notifications — bypass or otherwise.
    assert calls == []


def test_pretool_bypass_never_fires_when_not_chip_spawned(tmp_path):
    # Session marker absent → returns None BEFORE the bypass check runs.
    (tmp_path / gate.GATE_FLAG_FILENAME).touch()
    calls = []
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        tool_input={"prompt": "# spawn-task-bypass: test"},
        notify_fn=lambda t, m: calls.append((t, m)),
    )
    assert result is None
    assert calls == []


def test_pretool_bypass_token_notify_failure_never_breaks_deny(tmp_path):
    _arm_chip_session(tmp_path)
    def boom(*a, **kw):
        raise RuntimeError("notification service crashed")
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        tool_input={"prompt": "# spawn-task-bypass: test"},
        notify_fn=boom,
    )
    assert result is not None


def test_pretool_bypass_truncates_long_reason(tmp_path):
    _arm_chip_session(tmp_path)
    long_reason = "x" * 500
    calls = []
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        tool_input={"prompt": f"# spawn-task-bypass: {long_reason}"},
        notify_fn=lambda t, m: calls.append((t, m)),
    )
    assert result is not None
    assert len(calls) == 1
    _, message = calls[0]
    assert "cannot be overridden" in message


def test_pretool_no_bypass_when_tool_input_missing(tmp_path):
    """Absent tool_input → normal deny path (no bypass extraction possible)."""
    _arm_chip_session(tmp_path)
    result = gate.pretool_deny_or_none(
        tool_name="mcp__ccd_session__spawn_task",
        session_id="sid1", state_dir=tmp_path,
        # tool_input defaults to None
        notify_fn=_noop_notify,
    )
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
