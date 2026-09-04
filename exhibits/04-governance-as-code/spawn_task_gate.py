# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""spawn_task_gate — shared helper for the deterministic PreToolUse BLOCK on
`mcp__ccd_session__spawn_task` in chip-spawned sessions.

Design: Option 1 prompt-hash correlation (operator-approved 2026-07-25).

Parent PostToolUse writes an entry to `chip-spawns.jsonl` on every successful
`spawn_task` call. Child UserPromptSubmit (first turn only) cross-refs the
sha256 of its first user prompt against recent `chip-spawns.jsonl` entries and,
on match, writes `session-origins/<sid>.json` with `origin: chip`. PreToolUse
BLOCK reads that marker AND the activation flag; denies iff both are present.

Gate flag: `~/.claude/state/spawn-task-grandchild-gate.enabled` — absent =
BLOCK dormant (advisory WARN still fires from the pre-BLOCK function).
Present = BLOCK active. Rollback: `rm` the flag file (O(1), no code change).

Mandatory consumer bindings:
- `chip-spawns.jsonl`
    READ by `load_recent_chip_spawns` (both dispatchers' UserPromptSubmit)
    WRITTEN by `append_chip_spawn` (both dispatchers' PostToolUse spawn_task)
- `session-origins/<sid>.json`
    READ by `is_chip_spawned_session` (both dispatchers' PreToolUse spawn_task;
    daily-health `sessions.grandchild_chips`)
    WRITTEN by `maybe_write_session_origin` (both dispatchers' UserPromptSubmit)
- `spawn-task-grandchild-gate.enabled`
    READ by `is_gate_armed` (both dispatchers' PreToolUse spawn_task)
    WRITTEN ONLY by operator (~/.claude repo commit)

Fail-open discipline throughout: every branch returns False/None on error.
An outer try/except in `pretool_deny_or_none` guarantees no exception escapes
to the hook — a helper bug never wedges a session.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_STATE_DIR = Path.home() / ".claude" / "state"
CHIP_SPAWNS_FILENAME = "chip-spawns.jsonl"
SESSION_ORIGINS_DIRNAME = "session-origins"
SCANNED_MARKER_DIRNAME = "session-origins-scanned"
GATE_FLAG_FILENAME = "spawn-task-grandchild-gate.enabled"

CHIP_SPAWNS_RETENTION_SECONDS = 30 * 24 * 3600  # 30d — retention only; match is by hash

TASK_ID_RE = re.compile(r"task_id:\s*(task_[a-f0-9]+)")

# Request text is not bypass authority: a model controls its own prompt, title,
# and summary fields. The parser remains only for historical diagnostics/tests.
BYPASS_TOKEN_RE = re.compile(r"#\s*spawn-task-bypass:\s*([^\n]+)")

# Notify path — osascript is bounded to 3s so a stalled UserNotificationCenter
# never blocks the hook. Suppressed when SPAWN_TASK_GATE_NOTIFY_STUB is set
# (test mode; the stub path receives `title|message` lines for assertion).
NOTIFY_TIMEOUT_SECONDS = 3
NOTIFY_STUB_ENV = "SPAWN_TASK_GATE_NOTIFY_STUB"

BLOCK_MSG = (
    "spawn_task: this session was itself chip-spawned. Depth-1 chips only "
    "(feedback_defer_batchable_work_not_grandchild_chip). Batchable follow-on "
    "should go through `python3 ~/projects/scripts/defer_to_backlog.py deposit "
    "--repo <repo> --description \"<one line>\"` instead. Spawn a chip only for "
    "work needing an operator decision now. A chip-spawned session cannot "
    "override this gate through its own request text."
)


def _state_dir(state_dir: Path | None) -> Path:
    return Path(state_dir) if state_dir is not None else DEFAULT_STATE_DIR


def is_gate_armed(state_dir: Path | None = None) -> bool:
    """True iff the gate flag file exists. Any error → False (fail-open dormant)."""
    try:
        return (_state_dir(state_dir) / GATE_FLAG_FILENAME).exists()
    except Exception:
        return False


def is_chip_spawned_session(sid: str, state_dir: Path | None = None) -> bool:
    """True iff `session-origins/<sid>.json` exists AND parses AND has origin=='chip'.
    Any error → False (fail-open)."""
    if not sid:
        return False
    try:
        origin_file = _state_dir(state_dir) / SESSION_ORIGINS_DIRNAME / f"{sid}.json"
        if not origin_file.exists():
            return False
        data = json.loads(origin_file.read_text())
        return isinstance(data, dict) and data.get("origin") == "chip"
    except Exception:
        return False


def render_block_message(sid: str = "", parent_task_id: str | None = None) -> str:
    """Return the operator-facing deny reason. Byte-identical across both dispatchers."""
    return BLOCK_MSG


def extract_task_id(tool_result_text: Any) -> str | None:
    """Extract `task_id: task_XXXXXXXX` from a spawn_task tool_result text.
    Returns None on non-string input or parse miss (fail-open)."""
    if not isinstance(tool_result_text, str):
        return None
    m = TASK_ID_RE.search(tool_result_text)
    return m.group(1) if m else None


def prompt_sha256(prompt: str) -> str:
    """SHA-256 hex of the prompt's UTF-8 bytes. Exact-bytes match on both sides."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def append_chip_spawn(
    *,
    task_id: str,
    parent_session_id: str,
    prompt: str,
    state_dir: Path | None = None,
    now: float | None = None,
) -> bool:
    """Append a chip-spawn row to `chip-spawns.jsonl` with fcntl.flock.
    Prunes entries older than `CHIP_SPAWNS_RETENTION_SECONDS` on each write.
    Returns True on write, False on any error (fail-open — parent's PostToolUse
    hook never raises)."""
    if not task_id or not parent_session_id or prompt is None:
        return False
    try:
        d = _state_dir(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / CHIP_SPAWNS_FILENAME
        now_ts = now if now is not None else time.time()
        entry = {
            "task_id": task_id,
            "parent_session_id": parent_session_id,
            "prompt_sha256": prompt_sha256(prompt),
            "spawned_at": now_ts,
        }
        with open(path, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                fresh: list[str] = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    spawned_at = row.get("spawned_at") if isinstance(row, dict) else None
                    if (
                        isinstance(spawned_at, (int, float))
                        and (now_ts - spawned_at) < CHIP_SPAWNS_RETENTION_SECONDS
                    ):
                        fresh.append(json.dumps(row))
                fresh.append(json.dumps(entry))
                f.seek(0)
                f.truncate()
                f.write("\n".join(fresh) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def load_recent_chip_spawns(
    state_dir: Path | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Load chip-spawn entries within the retention window. Returns [] on any error."""
    try:
        path = _state_dir(state_dir) / CHIP_SPAWNS_FILENAME
        if not path.exists():
            return []
        now_ts = now if now is not None else time.time()
        entries: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            spawned_at = row.get("spawned_at") if isinstance(row, dict) else None
            if (
                isinstance(spawned_at, (int, float))
                and (now_ts - spawned_at) < CHIP_SPAWNS_RETENTION_SECONDS
            ):
                entries.append(row)
        return entries
    except Exception:
        return []


def maybe_write_session_origin(
    *,
    sid: str,
    first_user_prompt: str,
    state_dir: Path | None = None,
    now: float | None = None,
) -> str | None:
    """Called from child's UserPromptSubmit on first turn only (atomic-sentinel-guarded).
    Cross-refs sha256(first_user_prompt) against recent chip-spawns; on match writes
    `session-origins/<sid>.json` with origin=chip. Returns matched task_id on match,
    None otherwise (fail-open — hook never raises).

    First-turn semantics: creation of `session-origins-scanned/<sid>` is atomic via
    `open(path, 'x')` — second+ turns hit FileExistsError and return None.
    """
    if not sid or first_user_prompt is None:
        return None
    try:
        d = _state_dir(state_dir)
        scanned_dir = d / SCANNED_MARKER_DIRNAME
        scanned_dir.mkdir(parents=True, exist_ok=True)
        sentinel = scanned_dir / sid
        try:
            with open(sentinel, "x"):
                pass  # first invocation for this sid
        except FileExistsError:
            return None
        prompt_hash = prompt_sha256(first_user_prompt)
        for entry in load_recent_chip_spawns(state_dir=d, now=now):
            if entry.get("prompt_sha256") == prompt_hash:
                origin_dir = d / SESSION_ORIGINS_DIRNAME
                origin_dir.mkdir(parents=True, exist_ok=True)
                origin_file = origin_dir / f"{sid}.json"
                data = {
                    "origin": "chip",
                    "parent_task_id": entry.get("task_id"),
                    "parent_session_id": entry.get("parent_session_id"),
                    "matched_at": now if now is not None else time.time(),
                }
                origin_file.write_text(json.dumps(data))
                return entry.get("task_id")
        return None
    except Exception:
        return None


def extract_bypass_reason(tool_input: Any) -> str | None:
    """Scan the spawn_task tool_input's user-supplied text fields (prompt, title,
    tldr) for `# spawn-task-bypass: <reason>`. Returns the reason string (stripped)
    on first match, None otherwise. Fail-open on non-dict input or any error."""
    if not isinstance(tool_input, dict):
        return None
    try:
        for key in ("prompt", "title", "tldr"):
            v = tool_input.get(key)
            if not isinstance(v, str):
                continue
            m = BYPASS_TOKEN_RE.search(v)
            if m:
                reason = m.group(1).strip()
                if reason:
                    return reason
        return None
    except Exception:
        return None


def _default_notify(title: str, message: str) -> None:
    """Best-effort macOS notification via osascript. Bounded, fail-open.

    When SPAWN_TASK_GATE_NOTIFY_STUB is set, writes `title|message` to that
    path instead of firing the real notification (test mode). Any error is
    swallowed — the notification is advisory, never load-bearing."""
    try:
        stub = os.environ.get(NOTIFY_STUB_ENV)
        if stub:
            try:
                with open(stub, "a") as f:
                    f.write(f"{title}|{message}\n")
            except Exception:
                pass
            return
        # Escape backslashes and double-quotes for AppleScript string literal.
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            timeout=NOTIFY_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except Exception:
        pass


def _notify_block(sid: str, notify_fn: Callable[[str, str], None]) -> None:
    """Fire the block notification. Isolated so a notify-fn exception can't
    break the deny path (caller wraps in try/except)."""
    short = (sid or "unknown")[:8]
    title = "Chip spawn (grandchild) blocked"
    message = (
        f"session {short} — model will file to backlog or ask you. "
        "the gate cannot be overridden by request text."
    )
    notify_fn(title, message)


def _notify_bypass(sid: str, reason: str, notify_fn: Callable[[str, str], None]) -> None:
    """Fire the bypass notification so the operator sees a chip-spawn override
    happened out-of-band. Never blocks."""
    short = (sid or "unknown")[:8]
    title = "Chip spawn (grandchild) allowed via bypass"
    # Cap the reason to keep the notification body scannable.
    truncated = reason if len(reason) <= 120 else reason[:117] + "..."
    message = f"session {short}: {truncated}"
    notify_fn(title, message)


def pretool_deny_or_none(
    *,
    tool_name: str,
    session_id: str,
    tool_input: Any = None,
    state_dir: Path | None = None,
    notify_fn: Callable[[str, str], None] | None = None,
) -> dict[str, Any] | None:
    """PreToolUse handler entry. Returns the canonical deny dict shape iff
    tool_name == 'mcp__ccd_session__spawn_task' AND session is chip-spawned AND
    gate flag is armed. Returns None otherwise (fail-open). Request text never
    bypasses the gate because a model controls that text.

    Notification: when the block WOULD fire, a bounded osascript notification
    is fired via `notify_fn` (defaults to
    `_default_notify`). Notification failures are swallowed — the deny/allow
    decision is never affected.

    Outer try/except guarantees NO exception escapes to the hook."""
    try:
        if tool_name != "mcp__ccd_session__spawn_task":
            return None
        if not is_chip_spawned_session(session_id, state_dir=state_dir):
            return None
        if not is_gate_armed(state_dir=state_dir):
            return None
        notify = notify_fn if notify_fn is not None else _default_notify
        try:
            _notify_block(session_id, notify)
        except Exception:
            pass
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": render_block_message(session_id),
            }
        }
    except Exception:
        return None
