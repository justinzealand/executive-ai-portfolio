# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""chip_claim_gate — pre-flight advisory gate on unverified historical claims in
`mcp__ccd_session__spawn_task` prompts.

Incident (2026-08-30, session 6283533f). A chip prompt asserted "So BlueBubbles
is documented-but-never-installed on this host." That claim was inferred from a
grep of the CURRENT `~/.openclaw/openclaw.json` — a snapshot of present state
cannot support a claim about the past. Git disproved it: BlueBubbles was chosen
and installed 2026-03-17, ran on `*:1234` for ~2 months, and `~/.openclaw`
commit `f262dc3` (2026-05-18) removed the config. Worse, the chip's step 2
instructed the executor to WRITE that falsehood into `<workspace>/CLAUDE.md`.

Two structural facts make this unrecoverable after the fact:
  - `spawn_task` prompts are IMMUTABLE. There is no edit API.
  - `dismiss_task` only works while the user has not started the chip.
So the ONLY useful intervention is pre-send. Hence a PreToolUse gate.

Predicate (narrow by construction). Flag a sentence that makes an ABSOLUTE
HISTORICAL CLAIM (`never installed`, `was never`, `has always`, `is not
configured`, `no precedent`, `documented-but-never`, ...) WITHOUT a nearby
citation of HISTORICAL evidence (a commit SHA, a `git log`/`git show`/`git
blame`-family invocation, or an ISO date). Two exemptions keep the false
positive rate down and are load-bearing, not conveniences:

  1. HEDGED claims are correct behavior and never flag. "I think X was never
     installed — verify with git log" is exactly what a good chip says. A
     sentence carrying a hypothesis marker (`I think`, `probably`, `appears`,
     `unverified`, `suspect`, `verify`, `confirm`, ...) is exempt.
  2. CITED claims never flag. `never installed (removed in f262dc3)` carries
     its own evidence.

Modes. Resolution is a SINGLE switch — the flag file
`~/.claude/state/chip-claim-gate.enabled`:
  - absent  => WARN  (default). Never blocks. Fires a bounded operator
               notification naming the flagged sentence, appends a ledger row,
               and emits one structured stderr audit line.
  - present => BLOCK. Returns the canonical PreToolUse deny dict.
Rollback from BLOCK is `rm` on the flag file: O(1), no code change, no ship.

Promotion contract (WARN -> BLOCK). The operator arms the flag ONLY once
`promotion-status` reports ready, mirroring the HC8 pre-ship gate's thresholds
(`validate_consequential_write.py`) so the two soak contracts stay comparable:

    ready := adjudicated >= CHIP_CLAIM_GATE_SOAK_MIN_FLAGS (20)
             AND days_since_first_flag >= CHIP_CLAIM_GATE_SOAK_MIN_DAYS (14)
             AND fp_rate <= CHIP_CLAIM_GATE_FP_CEILING (0.05)

A flag becomes a false positive ONLY by explicit operator adjudication
(`adjudicate <flag_id> --false-positive`). The gate NEVER self-scores.
Unadjudicated rows are excluded from both numerator and denominator, so an
unreviewed backlog can never manufacture readiness.

Suppression marker. `# claim-verified: <reason>` in the prompt suppresses the
WARN — and fires an operator notification saying a suppression happened, so it
is never silent (feedback_ship_notify_bypass_before_arming_silent_gate). It is
recorded in the ledger as `suppressed: true`. It does NOT suppress a BLOCK:
per the sibling gate's contract, a model controls its own prompt text, so
request text is not bypass authority for a hard gate.

Fail-open discipline throughout. Every helper returns the allow-shaped value on
any exception, and `pretool_advisory_or_none` has an outer try/except so no
exception can escape to the hook. A guard bug never blocks a legitimate chip.

Mandatory consumer bindings:
- `chip-claim-flags.jsonl`
    WRITTEN by `append_flag_rows` (both dispatchers' PreToolUse spawn_task)
    READ by `load_flag_rows` (`promotion-status`, `adjudicate`)
- `chip-claim-gate.enabled`
    READ by `is_block_armed` (both dispatchers' PreToolUse spawn_task)
    WRITTEN ONLY by operator
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SPAWN_TASK_TOOL_NAME = "mcp__ccd_session__spawn_task"

DEFAULT_STATE_DIR = Path.home() / ".claude" / "state"
BLOCK_FLAG_FILENAME = "chip-claim-gate.enabled"

DEFAULT_LEDGER_DIR = Path.home() / ".automation-state" / "chip-claim-gate"
LEDGER_FILENAME = "chip-claim-flags.jsonl"
LEDGER_ENV_OVERRIDE = "CHIP_CLAIM_GATE_LEDGER"

# Promotion contract thresholds. Mirrored from validate_consequential_write.py's
# HC8 gate so the two soak contracts are directly comparable.
CHIP_CLAIM_GATE_SOAK_MIN_FLAGS = 20
CHIP_CLAIM_GATE_SOAK_MIN_DAYS = 14
CHIP_CLAIM_GATE_FP_CEILING = 0.05

# Characters of context on each side of a flagged sentence searched for a
# historical citation. "Nearby" is defined as a character window rather than a
# paragraph so the predicate is independent of markdown structure.
CITATION_WINDOW_CHARS = 400

# Fields of the spawn_task tool_input scanned. `title` is excluded: it is a
# <60-char imperative action phrase, too short to carry a claim plus its
# citation, so scanning it would be pure false-positive surface.
SCANNED_FIELDS = ("prompt", "tldr")

# Cap on flagged sentences reported per call, so a pathological prompt cannot
# produce an unbounded notification body or ledger burst.
MAX_FLAGGED_SENTENCES = 3
SENTENCE_DISPLAY_MAX_CHARS = 220

NOTIFY_TIMEOUT_SECONDS = 3
NOTIFY_STUB_ENV = "CHIP_CLAIM_GATE_NOTIFY_STUB"

SUPPRESSION_MARKER_RE = re.compile(r"#\s*claim-verified:\s*([^\n]+)")

# --- Predicate patterns ----------------------------------------------------
#
# ABSOLUTE historical assertions. Deliberately narrow: each alternative pins an
# absolute quantifier (`never`/`always`/`no precedent`) or a categorical
# installed/configured/present state claim. Bare `not` is NOT a member — "the
# file is not readable" is a present-tense observation, not a historical claim.
HISTORICAL_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("never_aux", r"\b(?:was|were|has|have|had|is|are)\s+never\b"),
    # `never_existed` is listed FIRST and owns existed/happened/shipped; those
    # alternatives are deliberately ABSENT from `never_verb` so a sentence can
    # never match two patterns and get an order-dependent label.
    ("never_existed", r"\bnever\s+(?:actually\s+)?(?:existed|happened|shipped)\b"),
    ("never_verb", r"\bnever\s+(?:been|installed|configured|set\s+up|used|run|ran|present|enabled|deployed|created|added)\b"),
    ("documented_but_never", r"\bdocumented[\s-]+but[\s-]+never\b"),
    ("always_aux", r"\b(?:was|were|has|have|had|is|are)\s+always\s+(?:been|had|used|run)\b"),
    ("no_precedent", r"\bno\s+precedent\b"),
    ("no_history", r"\bno\s+(?:history|record|trace|evidence)\s+of\b"),
    ("not_installed", r"\b(?:is|was|were|are)\s+not\s+(?:installed|configured|set\s+up|deployed|present|enabled)\b"),
    ("only_ever", r"\bonly\s+ever\s+(?:been|used|ran|run|existed)\b"),
)

_COMPILED_CLAIMS = tuple(
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in HISTORICAL_CLAIM_PATTERNS
)

# HEDGE markers. A sentence carrying any of these is a hypothesis, not an
# assertion, and is exempt (design constraint 3: "I think X was never installed
# — verify with git log" is CORRECT behavior and must not flag).
HEDGE_RE = re.compile(
    r"\b(?:i\s+think|i\s+believe|i\s+suspect|probably|likely|looks?\s+like|"
    r"sounds?\s+like|as\s+far\s+as\s+i\s+can\s+tell|"
    # `appears`/`seems` hedge ONLY when not introducing a LOCATION. "appears in
    # the docs" is a sighting, not a hypothesis — treating it as a hedge is what
    # let the incident's step-2 sentence ("appears in OpenClaw docs only and is
    # not installed") slip through the predicate during smoke.
    r"(?:appears?|seems?)\b(?!\s+(?:in|at|on|under|within|as|after|before|alongside|beside|throughout))|"
    r"may\s+(?:be|have|never)|might\s+(?:be|have|never)|"
    r"possibly|perhaps|unverified|unconfirmed|hypothesis|hypothesise|hypothesize|"
    r"assum(?:e|ing|ption)|suspect(?:ed)?|verify|confirm|double[\s-]check|"
    r"check\s+whether|not\s+sure|unclear|if\s+(?:it|this|that|so)\b|\?)",
    re.IGNORECASE,
)

# HISTORICAL EVIDENCE citation. A commit SHA (>=7 hex chars, at least one of
# which is a-f so a plain decimal like "1234567" never counts), a git
# history-reading subcommand, or an ISO date.
_SHA_RE = r"\b(?=[0-9a-f]{7,40}\b)[0-9a-f]*[a-f][0-9a-f]*\b"
_GIT_RE = r"\bgit\s+(?:log|show|blame|rev-list|cat-file|grep|diff|reflog|shortlog|whatchanged)\b"
_DATE_RE = r"\b\d{4}-\d{2}-\d{2}\b"
CITATION_RE = re.compile(f"(?:{_SHA_RE})|(?:{_GIT_RE})|(?:{_DATE_RE})", re.IGNORECASE)

# Fenced code blocks are stripped before scanning: a chip prompt routinely
# pastes config or command output containing "not installed", which is quoted
# EVIDENCE rather than an authored claim.
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)

BLOCK_MSG_HEADER = (
    "chip_claim_gate: this spawn_task prompt makes an absolute historical claim "
    "with no nearby historical evidence.\n\n"
    "A snapshot of CURRENT state cannot support a claim about the PAST. Verify "
    "with `git log`/`git show` (or an ISO-dated reference) and cite the evidence "
    "in the prompt, or hedge the claim explicitly (\"unverified — verify with "
    "git log\") and let the executor establish it.\n\n"
    "This matters because a spawned chip's prompt is IMMUTABLE: there is no edit "
    "API, and dismiss_task only works before the operator starts it.\n"
)


# --- state / ledger paths --------------------------------------------------

def _state_dir(state_dir: Path | None) -> Path:
    return Path(state_dir) if state_dir is not None else DEFAULT_STATE_DIR


def ledger_path(ledger: Path | None = None) -> Path:
    """Ledger location. Explicit arg > env override > default."""
    if ledger is not None:
        return Path(ledger)
    override = os.environ.get(LEDGER_ENV_OVERRIDE)
    if override:
        return Path(override)
    return DEFAULT_LEDGER_DIR / LEDGER_FILENAME


def is_block_armed(state_dir: Path | None = None) -> bool:
    """True iff the BLOCK flag file exists. Any error -> False (fail-open WARN)."""
    try:
        return (_state_dir(state_dir) / BLOCK_FLAG_FILENAME).exists()
    except Exception:
        return False


def resolve_mode(state_dir: Path | None = None) -> str:
    """'block' iff the flag file is present, else 'warn'. Single switch."""
    return "block" if is_block_armed(state_dir=state_dir) else "warn"


# --- pure predicate --------------------------------------------------------

def strip_code_fences(text: str) -> str:
    """Replace fenced code blocks with blank lines of equal line count.

    Quoted command output / config is evidence, not an authored claim. Line
    count is preserved so nothing downstream depends on fence position.
    """
    if not isinstance(text, str):
        return ""
    try:
        def _blank(m: re.Match) -> str:
            return "\n" * m.group(0).count("\n")
        return _FENCE_RE.sub(_blank, text)
    except Exception:
        return text


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Split into (start_offset, sentence) pairs.

    Splits on sentence-final punctuation followed by whitespace, and on
    newlines, so markdown bullets and headings each become their own unit. A
    claim and its citation living in two different bullets still resolve
    because the citation search uses a character window, not this split.
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        out: list[tuple[int, str]] = []
        pos = 0
        for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
            idx = text.find(chunk, pos)
            if idx < 0:
                idx = pos
            stripped = chunk.strip()
            if stripped:
                out.append((idx, stripped))
            pos = idx + len(chunk)
        return out
    except Exception:
        return []


def is_hedged(sentence: str) -> bool:
    """True iff the sentence reads as a hypothesis rather than an assertion."""
    try:
        return bool(HEDGE_RE.search(sentence))
    except Exception:
        return True  # fail-open: treat as hedged => do not flag


def has_nearby_citation(text: str, start: int, end: int,
                        window: int = CITATION_WINDOW_CHARS) -> bool:
    """True iff historical evidence appears within `window` chars either side."""
    try:
        lo = max(0, start - window)
        hi = min(len(text), end + window)
        return bool(CITATION_RE.search(text[lo:hi]))
    except Exception:
        return True  # fail-open: treat as cited => do not flag


def extract_suppression_reason(tool_input: Any) -> str | None:
    """`# claim-verified: <reason>` from any scanned field, else None."""
    if not isinstance(tool_input, dict):
        return None
    try:
        for key in SCANNED_FIELDS:
            v = tool_input.get(key)
            if not isinstance(v, str):
                continue
            m = SUPPRESSION_MARKER_RE.search(v)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return None
    except Exception:
        return None


def find_unverified_claims(text: str) -> list[dict[str, Any]]:
    """Core predicate. Returns one finding per flagged sentence.

    A sentence is flagged iff it matches an absolute-historical-claim pattern
    AND is not hedged AND has no historical citation within the char window.
    Any error returns [] (fail-open: no flag).
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return []
        scrubbed = strip_code_fences(text)
        findings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for start, sentence in split_sentences(scrubbed):
            if len(findings) >= MAX_FLAGGED_SENTENCES:
                break
            matched = None
            for name, rx in _COMPILED_CLAIMS:
                if rx.search(sentence):
                    matched = name
                    break
            if matched is None:
                continue
            if is_hedged(sentence):
                continue
            if has_nearby_citation(scrubbed, start, start + len(sentence)):
                continue
            key = sentence.strip()
            if key in seen:
                continue
            seen.add(key)
            findings.append({"sentence": key, "pattern": matched, "offset": start})
        return findings
    except Exception:
        return []


def scan_tool_input(tool_input: Any) -> list[dict[str, Any]]:
    """Run the predicate over every scanned field. Fail-open to []."""
    try:
        if not isinstance(tool_input, dict):
            return []
        out: list[dict[str, Any]] = []
        for key in SCANNED_FIELDS:
            v = tool_input.get(key)
            if not isinstance(v, str):
                continue
            for f in find_unverified_claims(v):
                out.append({**f, "field": key})
                if len(out) >= MAX_FLAGGED_SENTENCES:
                    return out
        return out
    except Exception:
        return []


# --- rendering -------------------------------------------------------------

def truncate(text: str, limit: int = SENTENCE_DISPLAY_MAX_CHARS) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 3] + "..."


def render_block_message(findings: list[dict[str, Any]]) -> str:
    lines = [BLOCK_MSG_HEADER, "Flagged:"]
    for f in findings:
        lines.append(f"  [{f.get('field', '?')}] {truncate(str(f.get('sentence', '')))}")
    lines.append(
        "\nThe `# claim-verified: <reason>` marker suppresses the advisory WARN "
        "only; it does not override this block, because a model controls its own "
        "prompt text. Cite the evidence or hedge the claim."
    )
    return "\n".join(lines)


def render_stderr_line(*, mode: str, session_id: str, findings: list[dict[str, Any]],
                       suppressed: bool = False, reason: str | None = None) -> str:
    """ONE structured audit line, mirroring registry_enforcement's shape."""
    outcome = "suppressed" if suppressed else ("block" if mode == "block" else "warn")
    first = truncate(str(findings[0].get("sentence", "")), 120) if findings else ""
    parts = [
        "chip_claim_gate:",
        f"outcome={outcome}",
        f"mode={mode}",
        f"count={len(findings)}",
        f"session={(session_id or 'unknown')[:8]}",
        f'sentence="{first}"',
    ]
    if reason:
        parts.append(f'suppression_reason="{truncate(reason, 80)}"')
    return " ".join(parts)


# --- notification ----------------------------------------------------------

def _default_notify(title: str, message: str) -> None:
    """Best-effort bounded macOS notification. Advisory, never load-bearing."""
    try:
        stub = os.environ.get(NOTIFY_STUB_ENV)
        if stub:
            try:
                with open(stub, "a") as f:
                    f.write(f"{title}|{message}\n")
            except Exception:
                pass
            return
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["/usr/bin/osascript", "-e",
             f'display notification "{safe_message}" with title "{safe_title}"'],
            timeout=NOTIFY_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except Exception:
        pass


def _notify_warn(findings: list[dict[str, Any]], notify_fn: Callable[[str, str], None]) -> None:
    title = "Chip prompt: unverified historical claim"
    body = truncate(str(findings[0].get("sentence", "")), 150) if findings else ""
    extra = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    notify_fn(title, f"{body}{extra} — dismiss the chip if wrong; prompts are immutable.")


def _notify_suppressed(reason: str, notify_fn: Callable[[str, str], None]) -> None:
    notify_fn(
        "Chip claim warning suppressed",
        f"# claim-verified: {truncate(reason, 120)}",
    )


# --- ledger ----------------------------------------------------------------

def compute_flag_id(prompt_text: str, sentence: str, flagged_at: str) -> str:
    payload = f"{hashlib.sha256((prompt_text or '').encode('utf-8')).hexdigest()}|{sentence}|{flagged_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append_flag_rows(rows: list[dict[str, Any]], ledger: Path | None = None) -> bool:
    """Append rows under an exclusive lock. Fail-open -> False."""
    if not rows:
        return False
    try:
        path = ledger_path(ledger)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        return True
    except Exception:
        return False


def load_flag_rows(ledger: Path | None = None) -> list[dict[str, Any]]:
    """Read all rows; malformed lines are skipped (never inflate readiness)."""
    try:
        path = ledger_path(ledger)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    out.append(row)
        return out
    except Exception:
        return []


def latest_by_flag_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse the append-only journal to one logical row per flag_id."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        fid = r.get("flag_id")
        if not isinstance(fid, str) or not fid:
            continue
        prev = out.get(fid)
        if prev is None:
            out[fid] = dict(r)
            continue
        merged = dict(prev)
        for k, v in r.items():
            if v is not None:
                merged[k] = v
        out[fid] = merged
    return out


def build_flag_rows(*, findings: list[dict[str, Any]], session_id: str,
                    tool_input: Any, mode: str, flagged_at: str,
                    suppressed: bool = False,
                    suppression_reason: str | None = None) -> list[dict[str, Any]]:
    prompt_text = ""
    title = ""
    if isinstance(tool_input, dict):
        prompt_text = tool_input.get("prompt") if isinstance(tool_input.get("prompt"), str) else ""
        title = tool_input.get("title") if isinstance(tool_input.get("title"), str) else ""
    rows = []
    for f in findings:
        sentence = str(f.get("sentence", ""))
        rows.append({
            "flag_id": compute_flag_id(prompt_text, sentence, flagged_at),
            "flagged_at": flagged_at,
            "mode": mode,
            "session_id": session_id or "",
            "task_title": truncate(title, 80),
            "field": f.get("field", "prompt"),
            "pattern": f.get("pattern", ""),
            "flagged_sentence": truncate(sentence, 400),
            "suppressed": bool(suppressed),
            "suppression_reason": suppression_reason,
            "operator_verdict": None,
        })
    return rows


# --- promotion contract ----------------------------------------------------

def compute_promotion_status(rows: list[dict[str, Any]], *, now: datetime | None = None,
                             mode: str = "warn") -> dict[str, Any]:
    """Deterministic readiness computation. Unadjudicated rows are excluded
    from BOTH numerator and denominator, so an unreviewed backlog can never
    manufacture readiness."""
    now = now or datetime.now(timezone.utc)
    logical = latest_by_flag_id(rows)
    verdicts = [r.get("operator_verdict") for r in logical.values()]
    false_positives = sum(1 for v in verdicts if v == "false_positive")
    true_positives = sum(1 for v in verdicts if v == "true_positive")
    adjudicated = false_positives + true_positives

    first_ts: datetime | None = None
    for r in logical.values():
        ts = r.get("flagged_at")
        if not isinstance(ts, str):
            continue
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if first_ts is None or parsed < first_ts:
            first_ts = parsed
    days_since_first = ((now - first_ts) / timedelta(days=1)) if first_ts else 0.0

    fp_rate = (false_positives / adjudicated) if adjudicated else 0.0
    ready = (
        adjudicated >= CHIP_CLAIM_GATE_SOAK_MIN_FLAGS
        and days_since_first >= CHIP_CLAIM_GATE_SOAK_MIN_DAYS
        and fp_rate <= CHIP_CLAIM_GATE_FP_CEILING
    )
    return {
        "mode": mode,
        "total_flags": len(logical),
        "adjudicated": adjudicated,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "fp_rate": round(fp_rate, 4),
        "days_since_first_flag": round(days_since_first, 2),
        "ready_to_promote": ready,
        "thresholds": {
            "soak_min_flags": CHIP_CLAIM_GATE_SOAK_MIN_FLAGS,
            "soak_min_days": CHIP_CLAIM_GATE_SOAK_MIN_DAYS,
            "fp_ceiling": CHIP_CLAIM_GATE_FP_CEILING,
        },
        "arm_command": f"touch {DEFAULT_STATE_DIR / BLOCK_FLAG_FILENAME}",
        "rollback_command": f"rm {DEFAULT_STATE_DIR / BLOCK_FLAG_FILENAME}",
    }


# --- hook entrypoint -------------------------------------------------------

def pretool_advisory_or_none(
    *,
    tool_name: str,
    session_id: str = "",
    tool_input: Any = None,
    state_dir: Path | None = None,
    ledger: Path | None = None,
    notify_fn: Callable[[str, str], None] | None = None,
    stderr_fn: Callable[[str], None] | None = None,
    now_fn: Callable[[], str] | None = None,
) -> dict[str, Any] | None:
    """PreToolUse handler entry.

    Returns the canonical deny dict ONLY when BLOCK is armed AND the predicate
    fires. In WARN mode (the default) it always returns None after firing the
    operator notification, appending the ledger row, and writing one stderr
    audit line — it never blocks a chip.

    Outer try/except guarantees NO exception escapes to the hook.
    """
    try:
        if tool_name != SPAWN_TASK_TOOL_NAME:
            return None
        findings = scan_tool_input(tool_input)
        if not findings:
            return None

        mode = resolve_mode(state_dir=state_dir)
        notify = notify_fn if notify_fn is not None else _default_notify
        emit = stderr_fn if stderr_fn is not None else (lambda s: sys.stderr.write(s + "\n"))
        flagged_at = now_fn() if now_fn else datetime.now(timezone.utc).isoformat()

        reason = extract_suppression_reason(tool_input)
        # Request text suppresses the advisory WARN only. It is never bypass
        # authority for a hard BLOCK, because a model controls its own prompt.
        suppressed = bool(reason) and mode != "block"

        rows = build_flag_rows(
            findings=findings, session_id=session_id, tool_input=tool_input,
            mode=mode, flagged_at=flagged_at, suppressed=suppressed,
            suppression_reason=reason,
        )
        try:
            append_flag_rows(rows, ledger=ledger)
        except Exception:
            pass
        try:
            emit(render_stderr_line(mode=mode, session_id=session_id,
                                    findings=findings, suppressed=suppressed,
                                    reason=reason))
        except Exception:
            pass
        try:
            if suppressed:
                _notify_suppressed(reason or "", notify)
            else:
                _notify_warn(findings, notify)
        except Exception:
            pass

        if mode != "block" or suppressed:
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": render_block_message(findings),
            }
        }
    except Exception:
        return None


# --- CLI -------------------------------------------------------------------

def _cmd_check(args: argparse.Namespace) -> int:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()
    findings = find_unverified_claims(text)
    payload = {
        "mode": resolve_mode(),
        "flagged": bool(findings),
        "count": len(findings),
        "findings": findings,
        "suppression_reason": extract_suppression_reason({"prompt": text}),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not findings:
            print("chip_claim_gate: clean — no unverified historical claims.")
        else:
            print(f"chip_claim_gate: {len(findings)} unverified historical claim(s)")
            for f in findings:
                print(f"  [{f['pattern']}] {truncate(f['sentence'])}")
            print("\nCite a commit SHA / `git log` / an ISO date, or hedge the claim.")
    return 1 if findings else 0


def _cmd_adjudicate(args: argparse.Namespace) -> int:
    rows = load_flag_rows()
    if args.flag_id not in latest_by_flag_id(rows):
        print(f"unknown flag_id: {args.flag_id}", file=sys.stderr)
        return 2
    verdict = "false_positive" if args.false_positive else "true_positive"
    ok = append_flag_rows([{
        "flag_id": args.flag_id,
        "operator_verdict": verdict,
        "adjudicated_at": datetime.now(timezone.utc).isoformat(),
    }])
    if not ok:
        print("failed to append adjudication row", file=sys.stderr)
        return 3
    print(json.dumps({"flag_id": args.flag_id, "operator_verdict": verdict}))
    return 0


def _cmd_promotion_status(args: argparse.Namespace) -> int:
    status = compute_promotion_status(load_flag_rows(), mode=resolve_mode())
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"mode                 : {status['mode']}")
        print(f"total flags          : {status['total_flags']}")
        print(f"adjudicated          : {status['adjudicated']} "
              f"(tp={status['true_positives']} fp={status['false_positives']})")
        print(f"fp rate              : {status['fp_rate']}")
        print(f"days since first flag: {status['days_since_first_flag']}")
        print(f"ready to promote     : {status['ready_to_promote']}")
        if status["ready_to_promote"] and status["mode"] != "block":
            print(f"\narm  : {status['arm_command']}")
            print(f"undo : {status['rollback_command']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chip_claim_gate",
        description="Pre-flight advisory gate on unverified historical claims in spawn_task prompts.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="scan prompt text (stdin, --text, or --file)")
    c.add_argument("--text")
    c.add_argument("--file")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=_cmd_check)

    a = sub.add_parser("adjudicate", help="operator marks a flag_id true/false positive")
    a.add_argument("flag_id")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--false-positive", action="store_true")
    g.add_argument("--true-positive", action="store_true")
    a.set_defaults(fn=_cmd_adjudicate)

    s = sub.add_parser("promotion-status", help="WARN->BLOCK readiness per the soak contract")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_promotion_status)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
