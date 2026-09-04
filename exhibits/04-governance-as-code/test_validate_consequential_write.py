# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Hermetic tests for the HC8 consequential-write pre-ship gate.

Blank-machine runnable: tmp fixtures, env-overridden ledger, injected tree/commit
shas and clock. No live workspace read, no production ledger touch.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "validate_consequential_write",
    str(Path(__file__).resolve().parent / "validate_consequential_write.py"),
)
vcw = importlib.util.module_from_spec(_SPEC)
sys.modules["validate_consequential_write"] = vcw  # so @dataclass resolves __module__
_SPEC.loader.exec_module(vcw)  # type: ignore


def test_datetime_usage_remains_python39_compatible():
    """The scheduled write gate runs with macOS's Python 3.9."""
    source = Path(vcw.__file__).read_text(encoding="utf-8")
    assert "from datetime import UTC" not in source
    assert "datetime.UTC" not in source


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    led = tmp_path / "fp-tally.jsonl"
    monkeypatch.setenv("HC8_GATE_LEDGER", str(led))
    return led


def _write(tmp_path: Path, name: str, body: str) -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


def _scan(tmp_path: Path, name: str) -> list[vcw.Finding]:
    return vcw.scan_paths(tmp_path, [name])


def _classes(findings) -> set[str]:
    return {f.flagged_class for f in findings}


# --------------------------------------------------------------------------- #
# detect-matrix: one confirmed match per (a)-(e)                              #
# --------------------------------------------------------------------------- #


def test_a_bulk_named_tool(tmp_path):
    _write(
        tmp_path,
        "w.py",
        "def create_multiple_spreadsheet_rows(sheet, rows):\n"
        "    Path('out.txt').write_text('done')\n"
        "create_multiple_spreadsheet_rows(sheet, rows)\n",
    )
    assert vcw.CLASS_A_BULK in _classes(_scan(tmp_path, "w.py"))


def test_a_bulk_named_helper_ignores_nested_write_sink(tmp_path):
    _write(
        tmp_path,
        "w.py",
        "def validate_surface_rows(rows):\n"
        "    def write_rows_later():\n"
        "        Path('out.txt').write_text('done')\n"
        "    return rows\n"
        "validate_surface_rows([])\n",
    )
    assert vcw.CLASS_A_BULK not in _classes(_scan(tmp_path, "w.py"))


def test_a_write_tool_in_loop(tmp_path):
    body = "for r in rows:\n    google_sheets_update_spreadsheet_row(sheet, r)\n"
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_A_BULK in _classes(_scan(tmp_path, "w.py"))


def test_a_bulk_generic_write_in_loop(tmp_path):
    # a known write tool that is NOT an external/destroy name, inside a loop → (a)
    body = "for r in rows:\n    google_sheets_update_spreadsheet_row(sheet, r)\n"
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_A_BULK in _classes(_scan(tmp_path, "w.py"))


def test_b_destroy_call(tmp_path):
    _write(tmp_path, "w.py", "google_drive_delete_file_permanent(fid)\n")
    assert vcw.CLASS_B_DESTROY in _classes(_scan(tmp_path, "w.py"))


def test_c_ddl_literal(tmp_path):
    _write(tmp_path, "w.py", 'conn.execute("ALTER TABLE ZACCOUNT DROP COLUMN x")\n')
    assert vcw.CLASS_C_SCHEMA in _classes(_scan(tmp_path, "w.py"))


def test_c_pragma_is_not_schema_ddl(tmp_path):
    _write(tmp_path, "w.py", 'conn.execute("PRAGMA busy_timeout = 30000")\n')
    assert vcw.CLASS_C_SCHEMA not in _classes(_scan(tmp_path, "w.py"))


def test_c_identifier_mutation(tmp_path):
    _write(tmp_path, "w.py", "notion_rename_database(db, name)\n")
    # rename_ substring
    assert vcw.CLASS_C_SCHEMA in _classes(_scan(tmp_path, "w.py"))


def test_c_identifier_mutation_beats_overlapping_external_advisory(tmp_path):
    _write(tmp_path, "w.py", "transfer_change_key(record, key)\n")
    classes = _classes(_scan(tmp_path, "w.py"))
    assert vcw.CLASS_C_SCHEMA in classes
    assert vcw.CLASS_E_EXTERNAL_ADVISORY not in classes


def test_d_protected_reference_literal(tmp_path):
    body = 'open("~/projects/DECISIONS.md", "w").write("x")\n'
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_D_REFERENCE in _classes(_scan(tmp_path, "w.py"))


def test_d_protected_write_text(tmp_path):
    body = 'Path("~/.codex/memories/MEMORY.md").write_text(new_body)\n'
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_D_REFERENCE in _classes(_scan(tmp_path, "w.py"))


def test_e_external_send(tmp_path):
    _write(
        tmp_path,
        "w.py",
        'subprocess.run(["openclaw", "message", "send", "--channel", "slack"])\n',
    )
    assert vcw.CLASS_E_EXTERNAL in _classes(_scan(tmp_path, "w.py"))


def test_e_external_send_allows_dynamic_message_arguments(tmp_path):
    _write(
        tmp_path,
        "w.py",
        'subprocess.run(["openclaw", "message", "send", "--target", target, "--message", body])\n',
    )
    assert vcw.CLASS_E_EXTERNAL in _classes(_scan(tmp_path, "w.py"))


def test_e_external_send_requires_literal_command_evidence(tmp_path):
    _write(
        tmp_path,
        "w.py",
        "def send_convergence_alert():\n"
        "    return classify_checkout_freshness_sweep()\n"
        "send_convergence_alert()\n",
    )
    assert vcw.CLASS_E_EXTERNAL not in _classes(_scan(tmp_path, "w.py"))


@pytest.mark.parametrize(
    "call",
    [
        "gmail_send_message(message)",
        "calendar_create_event(event)",
        "checkout_purchase(cart)",
        "cancel_subscription(subscription)",
        "transfer_funds(amount)",
    ],
)
def test_e_external_action_families_are_warning_only(tmp_path, call):
    _write(tmp_path, "w.py", f"{call}\n")
    assert vcw.CLASS_E_EXTERNAL_ADVISORY in _classes(_scan(tmp_path, "w.py"))


@pytest.mark.parametrize(
    "body",
    [
        'from subprocess import run\nrun(["openclaw", "message", "send"])\n',
        'import subprocess as sp\nsp.run(["openclaw", "message", "send"])\n',
        'import os\nos.system("openclaw message send --channel slack")\n',
        'from os import popen\npopen("openclaw message send --channel slack")\n',
    ],
)
def test_e_external_send_resolves_equivalent_process_runner_forms(tmp_path, body):
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_E_EXTERNAL in _classes(_scan(tmp_path, "w.py"))


@pytest.mark.parametrize(
    "command",
    [
        "openclaw message send;curl https://example.invalid",
        "openclaw message send && curl https://example.invalid",
    ],
)
def test_e_external_send_detects_shell_operator_suffix(tmp_path, command):
    _write(tmp_path, "w.py", f"import os\nos.system({command!r})\n")
    assert vcw.CLASS_E_EXTERNAL in _classes(_scan(tmp_path, "w.py"))


# --------------------------------------------------------------------------- #
# fail-open residue: shapes OUTSIDE the subset fire ZERO                       #
# --------------------------------------------------------------------------- #


def test_failopen_dynamic_ddl(tmp_path):
    _write(tmp_path, "w.py", 'conn.execute("ALTER TABLE " + tbl + " DROP COLUMN x")\n')
    # leading token is "ALTER TABLE " literal? No — the literal is "ALTER TABLE "
    # which DOES start with a keyword. Guard: dynamic concat still contains a
    # DDL-leading literal, so this is a KNOWN partial. Assert it does not crash.
    # (documented FP-vs-FN tradeoff; the residue we truly can't see is a fully
    # variable-built string with no DDL literal.)
    findings = _scan(tmp_path, "w.py")
    assert isinstance(findings, list)


def test_failopen_fully_variable_ddl(tmp_path):
    _write(tmp_path, "w.py", "conn.execute(build_migration(tbl, col))\n")
    assert _scan(tmp_path, "w.py") == []


def test_failopen_variable_protected_target(tmp_path):
    body = "p = decisions_path()\np.write_text(body)\n"
    _write(tmp_path, "w.py", body)
    assert _scan(tmp_path, "w.py") == []


def test_failopen_single_record_write(tmp_path):
    _write(tmp_path, "w.py", "notion_update_page(page_id, props)\n")
    # not bulk-named, not in a loop, not destroy/external → silent
    assert _scan(tmp_path, "w.py") == []


def test_failopen_read_of_protected(tmp_path):
    _write(tmp_path, "w.py", 'open("~/projects/DECISIONS.md").read()\n')
    assert _scan(tmp_path, "w.py") == []


def test_failopen_syntax_error(tmp_path):
    _write(tmp_path, "w.py", "def broken(:\n")
    assert _scan(tmp_path, "w.py") == []


def test_test_files_exempt(tmp_path):
    _write(tmp_path, "test_x.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    assert _scan(tmp_path, "test_x.py") == []


def test_suppression_marker(tmp_path):
    _write(
        tmp_path,
        "w.py",
        'subprocess.run(["openclaw", "message", "send"])  # hc8-gate-ok: verified digest\n',
    )
    assert _scan(tmp_path, "w.py") == []


def test_suppression_marker_in_string_does_not_suppress(tmp_path):
    body = (
        'x = "# hc8-gate-ok: not a real marker"\n'
        'subprocess.run(["openclaw", "message", "send"])\n'
    )
    _write(tmp_path, "w.py", body)
    assert vcw.CLASS_E_EXTERNAL in _classes(_scan(tmp_path, "w.py"))


# --------------------------------------------------------------------------- #
# shell scanner (bounded)                                                      #
# --------------------------------------------------------------------------- #


def test_shell_rm_rf(tmp_path):
    _write(tmp_path, "w.sh", "rm -rf /some/dir\n")
    assert vcw.CLASS_B_DESTROY in _classes(_scan(tmp_path, "w.sh"))


def test_shell_redirect_to_protected(tmp_path):
    _write(tmp_path, "w.sh", 'echo x > ~/projects/EXCEPTIONS.md\n')
    assert vcw.CLASS_D_REFERENCE in _classes(_scan(tmp_path, "w.sh"))


# --------------------------------------------------------------------------- #
# mode: confirmed external sends block; broader classes remain warn-only       #
# --------------------------------------------------------------------------- #


def _run_check(tmp_path, name, monkeypatch, mode):
    monkeypatch.setattr(vcw, "HC8_GATE_MODE", mode)
    ns = vcw.build_parser().parse_args(
        ["check", "--root", str(tmp_path), "--paths", name, "--staged-tree-sha", "T1", "--now", "2026-07-25T00:00:00+00:00"]
    )
    return vcw.cmd_check(ns)


def test_warn_mode_never_blocks(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", 'open("ordinary.txt", "w")\n')
    assert _run_check(tmp_path, "w.py", monkeypatch, "warn") == 0


def test_warn_mode_blocks_confirmed_external_send(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    assert _run_check(tmp_path, "w.py", monkeypatch, "warn") == 1


def test_warn_mode_keeps_name_only_external_action_advisory(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", "gmail_send_message(message)\n")
    assert _run_check(tmp_path, "w.py", monkeypatch, "warn") == 0


def test_block_mode_blocks_static_flag(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    assert _run_check(tmp_path, "w.py", monkeypatch, "block") == 1


def test_block_mode_blocks_reference_flag(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", 'open("~/projects/ALLOWLIST.md", "w")\n')
    assert _run_check(tmp_path, "w.py", monkeypatch, "block") == 1


def test_no_findings_never_blocks(tmp_path, monkeypatch):
    _write(tmp_path, "w.py", "notion_update_page(p, props)\n")
    assert _run_check(tmp_path, "w.py", monkeypatch, "block") == 0


# --------------------------------------------------------------------------- #
# ledger: check appends, keyed by flag_id                                      #
# --------------------------------------------------------------------------- #


def test_check_appends_null_commit_row(tmp_path, _ledger, monkeypatch):
    _write(tmp_path, "w.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    _run_check(tmp_path, "w.py", monkeypatch, "warn")
    rows = vcw._read_rows()
    assert len(rows) == 1
    assert rows[0]["commit_sha"] is None
    assert rows[0]["staged_tree_sha"] == "T1"
    assert rows[0]["operator_verdict"] == vcw.VERDICT_UNADJUDICATED
    assert rows[0]["flagged_class"] == vcw.CLASS_E_EXTERNAL
    assert rows[0]["path"] == "w.py"
    assert rows[0]["reason"] == "external send: openclaw message send"


def test_check_appends_one_row_per_same_class_finding(tmp_path, _ledger, monkeypatch):
    _write(
        tmp_path,
        "w.py",
        'subprocess.run(["openclaw", "message", "send", "--message", first])\n'
        'subprocess.run(["openclaw", "message", "send", "--message", second])\n',
    )
    _run_check(tmp_path, "w.py", monkeypatch, "warn")
    rows = vcw._read_rows()
    assert len(rows) == 2
    assert len({row["flag_id"] for row in rows}) == 2
    assert {row["line"] for row in rows} == {1, 2}


def test_check_paths_without_tree_does_not_append(tmp_path, _ledger, monkeypatch):
    _write(tmp_path, "w.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    monkeypatch.setattr(vcw, "HC8_GATE_MODE", "warn")
    ns = vcw.build_parser().parse_args(
        ["check", "--root", str(tmp_path), "--paths", "w.py", "--now", "2026-07-25T00:00:00+00:00"]
    )
    assert vcw.cmd_check(ns) == 1
    assert vcw._read_rows() == []


def test_check_no_ledger_does_not_append(tmp_path, _ledger, monkeypatch):
    _write(tmp_path, "w.py", 'subprocess.run(["openclaw", "message", "send"])\n')
    monkeypatch.setattr(vcw, "HC8_GATE_MODE", "warn")
    ns = vcw.build_parser().parse_args(
        [
            "check", "--root", str(tmp_path), "--paths", "w.py", "--staged-tree-sha", "T1",
            "--now", "2026-07-25T00:00:00+00:00", "--no-ledger",
        ]
    )
    assert vcw.cmd_check(ns) == 1
    assert vcw._read_rows() == []


# --------------------------------------------------------------------------- #
# backfill: EXACT tree binding (the P0 correlation contract)                   #
# --------------------------------------------------------------------------- #


def _seed_null(fid, tree, cls="hc8_e_external_irreversible", at="2026-07-01T00:00:00+00:00"):
    vcw._append_row(
        {
            "flag_id": fid,
            "staged_tree_sha": tree,
            "commit_sha": None,
            "flagged_class": cls,
            "flagged_at": at,
            "operator_verdict": vcw.VERDICT_UNADJUDICATED,
        }
    )


def _backfill(tmp_path, head_tree, head_sha):
    ns = vcw.build_parser().parse_args(
        ["backfill", "--root", str(tmp_path), "--head-tree-sha", head_tree, "--head-sha", head_sha, "--json"]
    )
    return vcw.cmd_backfill(ns)


def test_backfill_binds_only_matching_tree(tmp_path, _ledger):
    _seed_null("fidA", "TREE_A")
    _seed_null("fidB", "TREE_B")
    _backfill(tmp_path, "TREE_A", "COMMIT_A")
    latest = vcw._latest_by_flag(vcw._read_rows())
    assert latest["fidA"]["commit_sha"] == "COMMIT_A"
    assert latest["fidB"]["commit_sha"] is None  # never mis-attributed


def test_backfill_multiple_null_rows_only_matching(tmp_path, _ledger):
    # several unbackfilled null rows present; only the exact-tree one binds
    _seed_null("f1", "TREE_X")
    _seed_null("f2", "TREE_Y")
    _seed_null("f3", "TREE_X")
    _backfill(tmp_path, "TREE_X", "CX")
    latest = vcw._latest_by_flag(vcw._read_rows())
    assert latest["f1"]["commit_sha"] == "CX"
    assert latest["f3"]["commit_sha"] == "CX"
    assert latest["f2"]["commit_sha"] is None


def test_aborted_commit_leaves_never_matching_null(tmp_path, _ledger):
    # an aborted / --no-verify flag whose tree never becomes a real HEAD tree
    _seed_null("orphan", "TREE_NEVER")
    _backfill(tmp_path, "TREE_OTHER", "C")
    latest = vcw._latest_by_flag(vcw._read_rows())
    assert latest["orphan"]["commit_sha"] is None


def test_repeated_commit_same_tree(tmp_path, _ledger):
    _seed_null("f", "TREE_Z")
    _backfill(tmp_path, "TREE_Z", "C1")
    _backfill(tmp_path, "TREE_Z", "C2")  # idempotent-ish: binds again, still non-null
    latest = vcw._latest_by_flag(vcw._read_rows())
    assert latest["f"]["commit_sha"] is not None


# --------------------------------------------------------------------------- #
# promotion-status: pure readiness computation                                 #
# --------------------------------------------------------------------------- #


def _row(fid, verdict, committed=True, at="2026-06-01T00:00:00+00:00"):
    return {
        "flag_id": fid,
        "staged_tree_sha": "T",
        "commit_sha": "C" if committed else None,
        "flagged_class": "hc8_e_external_irreversible",
        "flagged_at": at,
        "operator_verdict": verdict,
    }


def test_promotion_not_ready_below_min_commits():
    rows = [_row(f"f{i}", vcw.VERDICT_TRUE) for i in range(5)]
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    st = vcw.compute_promotion_status(rows, now)
    assert st["ready"] is False
    assert st["adjudicated_count"] == 5


def test_promotion_ready_when_all_conditions_met():
    rows = [_row(f"f{i}", vcw.VERDICT_TRUE) for i in range(20)]
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)  # first flag 2026-06-01 → >14d
    st = vcw.compute_promotion_status(rows, now)
    assert st["ready"] is True
    assert st["fp_rate"] == 0.0


def test_promotion_fp_ceiling_blocks_flip():
    rows = [_row(f"t{i}", vcw.VERDICT_TRUE) for i in range(18)]
    rows += [_row("fp1", vcw.VERDICT_FALSE), _row("fp2", vcw.VERDICT_FALSE)]
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    st = vcw.compute_promotion_status(rows, now)
    # 2/20 = 0.10 > 0.05
    assert st["ready"] is False
    assert st["fp_rate"] == 0.1


def test_promotion_days_condition_blocks_flip():
    rows = [_row(f"f{i}", vcw.VERDICT_TRUE, at="2026-07-20T00:00:00+00:00") for i in range(20)]
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)  # only 5 days
    st = vcw.compute_promotion_status(rows, now)
    assert st["ready"] is False


def test_unadjudicated_null_rows_excluded_from_count():
    rows = [_row(f"f{i}", vcw.VERDICT_TRUE) for i in range(20)]
    rows += [_row(f"u{i}", vcw.VERDICT_UNADJUDICATED, committed=False) for i in range(50)]
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    st = vcw.compute_promotion_status(rows, now)
    assert st["adjudicated_count"] == 20  # the 50 unadjudicated never inflate it
    assert st["ready"] is True


def test_gc_stale_unadjudicated_null_rows():
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    stale = (now - timedelta(days=40)).isoformat()
    rows = [_row("old", vcw.VERDICT_UNADJUDICATED, committed=False, at=stale)]
    st = vcw.compute_promotion_status(rows, now)
    assert st["gc_excluded_stale_null_rows"] == 1
    assert st["adjudicated_count"] == 0


def test_malformed_ledger_row_skipped(tmp_path, _ledger):
    _ledger.write_text('{"flag_id": "ok", "operator_verdict": "true_positive", "flagged_at": "2026-06-01T00:00:00+00:00", "commit_sha": "C"}\nnot-json\n{bad\n', encoding="utf-8")
    rows = vcw._read_rows()
    assert len(rows) == 1  # the two malformed lines dropped
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    st = vcw.compute_promotion_status(rows, now)
    assert st["adjudicated_count"] == 1


def test_latest_adjudication_wins():
    rows = [
        _row("f", vcw.VERDICT_UNADJUDICATED),
        _row("f", vcw.VERDICT_TRUE),
        _row("f", vcw.VERDICT_FALSE),
    ]
    latest = vcw._latest_by_flag(rows)
    assert latest["f"]["operator_verdict"] == vcw.VERDICT_FALSE


# --------------------------------------------------------------------------- #
# adjudicate subcommand                                                        #
# --------------------------------------------------------------------------- #


def test_adjudicate_appends_verdict_row(tmp_path, _ledger):
    _seed_null("fidX", "T")
    ns = vcw.build_parser().parse_args(["adjudicate", "fidX", "--false-positive", "--json"])
    assert vcw.cmd_adjudicate(ns) == 0
    latest = vcw._latest_by_flag(vcw._read_rows())
    assert latest["fidX"]["operator_verdict"] == vcw.VERDICT_FALSE


def test_adjudicate_unknown_flag_id(tmp_path, _ledger):
    ns = vcw.build_parser().parse_args(["adjudicate", "nope", "--true-positive"])
    assert vcw.cmd_adjudicate(ns) == 2


# --------------------------------------------------------------------------- #
# flag_id determinism + main fail-open                                         #
# --------------------------------------------------------------------------- #


def test_flag_id_deterministic():
    finding = vcw.Finding("hc8_a_bulk", "a.py", 10, "bulk write call: create_rows")
    a = vcw.compute_flag_id("T", finding, "2026-07-25T00:00:00+00:00")
    b = vcw.compute_flag_id("T", finding, "2026-07-25T00:00:00+00:00")
    assert a == b
    c = vcw.compute_flag_id(
        "T",
        vcw.Finding("hc8_a_bulk", "a.py", 11, "bulk write call: create_rows"),
        "2026-07-25T00:00:00+00:00",
    )
    assert a != c


def test_gate_does_not_self_flag(tmp_path):
    # the gate's own source carries _DDL_KEYWORDS etc. as data — must self-exempt
    gate = Path(vcw.__file__)
    assert vcw.scan_file(gate, gate.name) == []


def test_main_failopen_on_internal_error(monkeypatch, tmp_path):
    # a check against a nonexistent root path never raises out
    rc = vcw.main(["check", "--root", str(tmp_path / "nope"), "--paths", "x.py"])
    assert rc == 0
