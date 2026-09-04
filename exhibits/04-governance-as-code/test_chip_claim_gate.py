# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Tests for chip_claim_gate — the pre-flight advisory gate on unverified
historical claims in spawn_task prompts.

The two INCIDENT fixtures are the verbatim sentences from the 2026-08-30
chip `task_d1344f25` (session 6283533f). They are the regression anchor: the
predicate exists because these two shipped.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chip_claim_gate as g  # noqa: E402


# --- verbatim incident fixtures --------------------------------------------

INCIDENT_CLAIM = (
    "I grepped ~/.openclaw/openclaw.json and found no channels.bluebubbles "
    "entry. So BlueBubbles is documented-but-never-installed on this host."
)
INCIDENT_STEP2 = (
    "Update the bullet to state the actual situation: BlueBubbles appears in "
    "OpenClaw docs only and is not installed."
)


def _flag(text: str) -> bool:
    return bool(g.find_unverified_claims(text))


# --- predicate: true positives ---------------------------------------------

def test_incident_claim_sentence_is_flagged():
    findings = g.find_unverified_claims(INCIDENT_CLAIM)
    assert findings, "the 2026-08-30 incident sentence must flag"
    assert findings[0]["pattern"] == "documented_but_never"


def test_incident_step2_instruction_is_flagged():
    """The step-2 instruction is the worse half: it told the executor to WRITE
    the falsehood into CLAUDE.md. 'appears in <docs>' is a sighting, not a
    hedge, and must not exempt it."""
    assert _flag(INCIDENT_STEP2)


@pytest.mark.parametrize("text,pattern", [
    ("BlueBubbles was never installed on this host.", "never_aux"),
    ("The plugin has never been enabled.", "never_aux"),
    ("This channel never existed in production.", "never_existed"),
    ("There is no precedent for a headless session loading that plugin.", "no_precedent"),
    ("There is no record of that job ever firing.", "no_history"),
    ("The gateway has always been the only writer of that table.", "always_aux"),
    ("The service is not configured on this machine.", "not_installed"),
    ("It has only ever been used for local testing.", "only_ever"),
    ("The proxy was never deployed.", "never_aux"),
])
def test_absolute_historical_claims_flag(text, pattern):
    findings = g.find_unverified_claims(text)
    assert findings, f"expected a flag for: {text}"
    assert findings[0]["pattern"] == pattern


# --- predicate: hedge exemption (design constraint 3) -----------------------

@pytest.mark.parametrize("text", [
    "I think BlueBubbles was never installed — verify with git log.",
    "It appears the config was never present.",
    "It seems BlueBubbles was never installed.",
    "BlueBubbles was probably never installed here.",
    "Unverified: the plugin has never been enabled.",
    "I suspect it was never configured; please confirm.",
    "This may never have been installed.",
    "Was it ever installed? It looks like it was never present.",
    "Assuming it was never installed, update the bullet.",
    "Check whether the service is not installed before editing.",
])
def test_hedged_claims_never_flag(text):
    """A hypothesis is CORRECT chip-authoring behavior. Flagging it would
    punish exactly the phrasing the gate is trying to encourage."""
    assert not _flag(text)


# --- predicate: citation exemption ------------------------------------------

@pytest.mark.parametrize("text", [
    "BlueBubbles was never re-enabled after commit f262dc3 removed the config.",
    "Run `git log --all -S bluebubbles`. The channel was never restored.",
    "Removed 2026-05-18; the channel has never been re-added since.",
    "Per `git show f262dc3`, the plugin was never reinstated.",
    "`git blame` on that line shows it was never touched after the rewrite.",
])
def test_cited_claims_never_flag(text):
    assert not _flag(text)


def test_citation_counts_within_window_across_sentences():
    text = (
        "See `git log --all -S bluebubbles` for the full history of this channel "
        "and the surrounding plugin work.\n\nIt was never re-enabled afterwards."
    )
    assert not _flag(text)


def test_citation_outside_window_does_not_exempt():
    text = (
        "Run `git log --all -S bluebubbles` first.\n\n"
        + ("filler prose that pads the distance well past the window. " * 20)
        + "\n\nBlueBubbles was never installed."
    )
    assert _flag(text)


def test_decimal_number_is_not_a_sha():
    """A 7-digit decimal must not launder as a commit SHA — the SHA pattern
    requires at least one hex letter."""
    assert _flag("Ticket 1234567 says the plugin was never installed.")


def test_hex_sha_is_a_citation():
    assert not _flag("Per f262dc3 the plugin was never installed.")


# --- predicate: negatives ---------------------------------------------------

@pytest.mark.parametrize("text", [
    "Fix the stale bullet in projects/CLAUDE.md and add a regression test.",
    "The file is not readable under launchd because of TCC.",
    "Update the docstring to describe the new fail-open branch.",
    "Add a test asserting the collector degrades to cannot-verify.",
    "",
    "   ",
])
def test_ordinary_prose_never_flags(text):
    assert not _flag(text)


def test_code_fence_content_is_stripped():
    """Quoted command output is evidence, not an authored claim."""
    text = "Check the output:\n```\nbluebubbles: is not installed\n```\nThen report."
    assert not _flag(text)


def test_claim_outside_fence_still_flags():
    text = "```\nsome config\n```\nBlueBubbles was never installed."
    assert _flag(text)


def test_findings_are_capped():
    text = "\n".join(f"Service {i} was never installed." for i in range(20))
    assert len(g.find_unverified_claims(text)) == g.MAX_FLAGGED_SENTENCES


def test_duplicate_sentences_dedupe():
    s = "BlueBubbles was never installed."
    assert len(g.find_unverified_claims(f"{s}\n{s}\n{s}")) == 1


# --- fail-open --------------------------------------------------------------

@pytest.mark.parametrize("value", [None, 123, [], {}, object()])
def test_non_string_input_fails_open(value):
    assert g.find_unverified_claims(value) == []


def test_scan_tool_input_ignores_non_dict():
    assert g.scan_tool_input("not a dict") == []
    assert g.scan_tool_input(None) == []


def test_title_is_not_scanned():
    """`title` is a <60-char action phrase — pure FP surface, deliberately out."""
    ti = {"title": "BlueBubbles was never installed", "prompt": "Do the thing."}
    assert g.scan_tool_input(ti) == []


def test_tldr_is_scanned():
    ti = {"prompt": "Do the thing.", "tldr": "BlueBubbles was never installed."}
    found = g.scan_tool_input(ti)
    assert found and found[0]["field"] == "tldr"


def test_predicate_exception_fails_open(monkeypatch):
    monkeypatch.setattr(g, "split_sentences", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    assert g.find_unverified_claims(INCIDENT_CLAIM) == []


def test_hedge_check_exception_fails_open_to_hedged(monkeypatch):
    """A broken hedge check must degrade to 'treat as hedged' (no flag), never
    to 'treat as assertion' (block a legitimate chip)."""
    class Boom:
        def search(self, _):
            raise RuntimeError("boom")
    monkeypatch.setattr(g, "HEDGE_RE", Boom())
    assert g.is_hedged("anything") is True


def test_citation_check_exception_fails_open_to_cited(monkeypatch):
    class Boom:
        def search(self, _):
            raise RuntimeError("boom")
    monkeypatch.setattr(g, "CITATION_RE", Boom())
    assert g.has_nearby_citation("x", 0, 1) is True


# --- mode resolution --------------------------------------------------------

def test_mode_defaults_to_warn(tmp_path):
    assert g.resolve_mode(state_dir=tmp_path) == "warn"
    assert g.is_block_armed(state_dir=tmp_path) is False


def test_mode_is_block_when_flag_present(tmp_path):
    (tmp_path / g.BLOCK_FLAG_FILENAME).write_text("")
    assert g.resolve_mode(state_dir=tmp_path) == "block"


def test_is_block_armed_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(g, "_state_dir", lambda _: (_ for _ in ()).throw(RuntimeError()))
    assert g.is_block_armed() is False


# --- hook entrypoint --------------------------------------------------------

def _call(tool_input, *, state_dir, ledger, notes=None, errs=None, sid="sess1234abcd"):
    return g.pretool_advisory_or_none(
        tool_name=g.SPAWN_TASK_TOOL_NAME,
        session_id=sid,
        tool_input=tool_input,
        state_dir=state_dir,
        ledger=ledger,
        notify_fn=(lambda t, m: notes.append((t, m))) if notes is not None else (lambda t, m: None),
        stderr_fn=(lambda s: errs.append(s)) if errs is not None else (lambda s: None),
        now_fn=lambda: "2026-08-30T12:00:00+00:00",
    )


def test_warn_mode_never_blocks(tmp_path):
    ledger = tmp_path / "flags.jsonl"
    notes, errs = [], []
    out = _call({"prompt": INCIDENT_CLAIM}, state_dir=tmp_path, ledger=ledger,
                notes=notes, errs=errs)
    assert out is None, "WARN mode must never return a deny dict"
    assert notes, "operator notification must fire"
    assert errs and errs[0].startswith("chip_claim_gate: outcome=warn")
    assert ledger.exists()


def test_block_mode_returns_canonical_deny_dict(tmp_path):
    (tmp_path / g.BLOCK_FLAG_FILENAME).write_text("")
    out = _call({"prompt": INCIDENT_CLAIM}, state_dir=tmp_path, ledger=tmp_path / "f.jsonl")
    assert out is not None
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "documented-but-never-installed" in hso["permissionDecisionReason"]


def test_other_tools_are_ignored(tmp_path):
    assert g.pretool_advisory_or_none(
        tool_name="Bash", session_id="s", tool_input={"prompt": INCIDENT_CLAIM},
        state_dir=tmp_path, ledger=tmp_path / "f.jsonl",
    ) is None


def test_clean_prompt_writes_nothing(tmp_path):
    ledger = tmp_path / "flags.jsonl"
    notes, errs = [], []
    out = _call({"prompt": "Fix the stale bullet and add a test."},
                state_dir=tmp_path, ledger=ledger, notes=notes, errs=errs)
    assert out is None
    assert not ledger.exists()
    assert notes == [] and errs == []


def test_entrypoint_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "scan_tool_input", lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _call({"prompt": INCIDENT_CLAIM}, state_dir=tmp_path, ledger=tmp_path / "f.jsonl") is None


def test_notify_failure_does_not_affect_decision(tmp_path):
    (tmp_path / g.BLOCK_FLAG_FILENAME).write_text("")
    def boom(t, m):
        raise RuntimeError("notify down")
    out = g.pretool_advisory_or_none(
        tool_name=g.SPAWN_TASK_TOOL_NAME, session_id="s",
        tool_input={"prompt": INCIDENT_CLAIM}, state_dir=tmp_path,
        ledger=tmp_path / "f.jsonl", notify_fn=boom, stderr_fn=lambda s: None,
    )
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- suppression marker -----------------------------------------------------

def test_marker_suppresses_warn_but_still_notifies(tmp_path):
    ledger = tmp_path / "flags.jsonl"
    notes, errs = [], []
    # NOTE: the reason string must not itself contain a citation token
    # (`git log`, a SHA, an ISO date) or the citation window exempts the
    # sentence and the gate returns before the suppression path is reached.
    prompt = INCIDENT_CLAIM + "\n\n# claim-verified: operator confirmed offline"
    out = _call({"prompt": prompt}, state_dir=tmp_path, ledger=ledger, notes=notes, errs=errs)
    assert out is None
    assert notes and "suppressed" in notes[0][0].lower(), "suppression must never be silent"
    assert errs[0].startswith("chip_claim_gate: outcome=suppressed")
    rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    assert rows[0]["suppressed"] is True
    assert rows[0]["suppression_reason"] == "operator confirmed offline"


def test_marker_does_not_bypass_block(tmp_path):
    """Request text is not bypass authority for a hard gate — a model controls
    its own prompt (spawn_task_gate.py's contract)."""
    (tmp_path / g.BLOCK_FLAG_FILENAME).write_text("")
    prompt = INCIDENT_CLAIM + "\n\n# claim-verified: trust me"
    out = _call({"prompt": prompt}, state_dir=tmp_path, ledger=tmp_path / "f.jsonl")
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_marker_requires_a_reason():
    assert g.extract_suppression_reason({"prompt": "# claim-verified:   "}) is None
    assert g.extract_suppression_reason({"prompt": "# claim-verified: ok"}) == "ok"
    assert g.extract_suppression_reason("not a dict") is None


# --- ledger + promotion contract --------------------------------------------

def test_ledger_row_shape(tmp_path):
    ledger = tmp_path / "flags.jsonl"
    _call({"prompt": INCIDENT_CLAIM, "title": "Fix stale claim"},
          state_dir=tmp_path, ledger=ledger)
    row = json.loads(ledger.read_text().splitlines()[0])
    for key in ("flag_id", "flagged_at", "mode", "session_id", "task_title",
                "field", "pattern", "flagged_sentence", "suppressed",
                "operator_verdict"):
        assert key in row
    assert row["operator_verdict"] is None
    assert row["task_title"] == "Fix stale claim"
    assert len(row["flag_id"]) == 16


def test_flag_id_is_stable_and_distinguishing():
    a = g.compute_flag_id("p", "s", "t")
    assert a == g.compute_flag_id("p", "s", "t")
    assert a != g.compute_flag_id("p2", "s", "t")
    assert a != g.compute_flag_id("p", "s2", "t")
    assert a != g.compute_flag_id("p", "s", "t2")


def test_append_failure_is_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "ledger_path", lambda l=None: (_ for _ in ()).throw(RuntimeError()))
    assert g.append_flag_rows([{"flag_id": "x"}]) is False


def test_malformed_ledger_lines_are_skipped(tmp_path, monkeypatch):
    p = tmp_path / "f.jsonl"
    p.write_text('{"flag_id":"a"}\nNOT JSON\n[1,2]\n{"flag_id":"b"}\n')
    monkeypatch.setenv(g.LEDGER_ENV_OVERRIDE, str(p))
    rows = g.load_flag_rows()
    assert [r["flag_id"] for r in rows] == ["a", "b"]


def test_latest_by_flag_id_merges_adjudication():
    rows = [
        {"flag_id": "a", "flagged_at": "t", "operator_verdict": None},
        {"flag_id": "a", "operator_verdict": "false_positive"},
    ]
    merged = g.latest_by_flag_id(rows)
    assert merged["a"]["operator_verdict"] == "false_positive"
    assert merged["a"]["flagged_at"] == "t", "backfill must not drop original fields"


def _rows(n, verdict, *, days_ago=30):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return [{"flag_id": f"f{i}", "flagged_at": ts, "operator_verdict": verdict}
            for i in range(n)]


def test_promotion_not_ready_when_unadjudicated():
    """An unreviewed backlog must never manufacture readiness."""
    st = g.compute_promotion_status(_rows(50, None))
    assert st["adjudicated"] == 0
    assert st["ready_to_promote"] is False


def test_promotion_not_ready_below_flag_count():
    st = g.compute_promotion_status(_rows(5, "true_positive"))
    assert st["ready_to_promote"] is False


def test_promotion_not_ready_below_soak_days():
    st = g.compute_promotion_status(_rows(25, "true_positive", days_ago=3))
    assert st["ready_to_promote"] is False


def test_promotion_not_ready_above_fp_ceiling():
    rows = _rows(20, "true_positive") + [
        {"flag_id": "fp1", "flagged_at": _rows(1, None)[0]["flagged_at"],
         "operator_verdict": "false_positive"} for _ in range(1)
    ]
    rows += [{"flag_id": f"fpx{i}", "flagged_at": rows[0]["flagged_at"],
              "operator_verdict": "false_positive"} for i in range(5)]
    st = g.compute_promotion_status(rows)
    assert st["fp_rate"] > g.CHIP_CLAIM_GATE_FP_CEILING
    assert st["ready_to_promote"] is False


def test_promotion_ready_when_all_thresholds_met():
    st = g.compute_promotion_status(_rows(20, "true_positive"))
    assert st["adjudicated"] == 20
    assert st["fp_rate"] == 0.0
    assert st["days_since_first_flag"] >= g.CHIP_CLAIM_GATE_SOAK_MIN_DAYS
    assert st["ready_to_promote"] is True


def test_promotion_status_reports_arm_and_rollback():
    st = g.compute_promotion_status([])
    assert g.BLOCK_FLAG_FILENAME in st["arm_command"]
    assert st["rollback_command"].startswith("rm ")


def test_promotion_tolerates_unparseable_timestamps():
    st = g.compute_promotion_status([{"flag_id": "a", "flagged_at": "nonsense",
                                      "operator_verdict": "true_positive"}])
    assert st["days_since_first_flag"] == 0.0
    assert st["ready_to_promote"] is False


# --- rendering --------------------------------------------------------------

def test_stderr_line_is_single_line_and_structured():
    line = g.render_stderr_line(mode="warn", session_id="abcdefgh1234",
                                findings=[{"sentence": "X was never installed."}])
    assert "\n" not in line
    assert "outcome=warn" in line and "mode=warn" in line and "session=abcdefgh" in line


def test_block_message_names_the_immutability_and_the_sentence():
    msg = g.render_block_message([{"field": "prompt", "sentence": INCIDENT_CLAIM}])
    assert "IMMUTABLE" in msg
    assert "dismiss_task" in msg
    assert "git log" in msg


# --- CLI --------------------------------------------------------------------

def _cli(*args, env=None):
    import os
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "chip_claim_gate.py"), *args],
        capture_output=True, text=True, env=e,
    )


def test_cli_check_flags_incident_with_exit_1():
    r = _cli("check", "--text", INCIDENT_CLAIM, "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout)["flagged"] is True


def test_cli_check_clean_exits_0():
    r = _cli("check", "--text", "Fix the bullet and add a test.", "--json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["flagged"] is False


def test_cli_check_reads_stdin():
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "chip_claim_gate.py"),
         "check", "--json"],
        input=INCIDENT_CLAIM, capture_output=True, text=True,
    )
    assert json.loads(r.stdout)["count"] == 1


def test_cli_adjudicate_round_trip(tmp_path):
    ledger = tmp_path / "f.jsonl"
    env = {g.LEDGER_ENV_OVERRIDE: str(ledger)}
    g.append_flag_rows([{"flag_id": "deadbeefdeadbeef", "flagged_at":
                         datetime.now(timezone.utc).isoformat(),
                         "operator_verdict": None}], ledger=ledger)
    r = _cli("adjudicate", "deadbeefdeadbeef", "--false-positive", env=env)
    assert r.returncode == 0
    st = json.loads(_cli("promotion-status", "--json", env=env).stdout)
    assert st["false_positives"] == 1 and st["adjudicated"] == 1


def test_cli_adjudicate_unknown_flag_id_errors(tmp_path):
    env = {g.LEDGER_ENV_OVERRIDE: str(tmp_path / "f.jsonl")}
    r = _cli("adjudicate", "nope", "--true-positive", env=env)
    assert r.returncode == 2


def test_cli_promotion_status_json(tmp_path):
    env = {g.LEDGER_ENV_OVERRIDE: str(tmp_path / "none.jsonl")}
    st = json.loads(_cli("promotion-status", "--json", env=env).stdout)
    assert st["ready_to_promote"] is False and st["total_flags"] == 0
