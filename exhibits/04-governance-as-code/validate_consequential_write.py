#!/usr/bin/env python3
# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""HC8 consequential-write pre-ship gate — static detector + promotion ledger.

Shift-left gate for the statically-detectable subset of charter Hard Constraint 8
(consequential writes). Sibling of ``validate_cron_contract.py`` /
``sweep_parity_precommit.py`` / ``wip_subject_precommit.py``.

Authoritative scope: ``docs/architecture/records/hc8-consequential-write-preship-gate.md``
(the AST/call-site pattern set per HC8 (a)-(e) + the enumerated (d)
protected-reference-path registry). Plan: ``docs/plans/hc8-consequential-write-preship-gate.md``.

Design invariants (converged over the /plan-review passes):
  * Confirmed-match-only — a shape fires only when decidable from the AST /
    call-site alone (a literal tool name, a DDL string literal, an enumerated
    path literal). Anything needing a runtime value fails OPEN (silent).
  * Reuse the taxonomy — the write-tool name set is read live from
    ``registry_enforcement.MCP_MUTATING_TOOLS``; a flagged class routes through
    ``registry_enforcement.load_exceptions`` + ``load_allowlist``. No hand-copied
    tool list, no new registry reader.
  * Most classes remain WARN-only. A confirmed external-send match blocks
    immediately; it is a concrete consequential action rather than an advisory
    signal. Broader WARN->block promotion remains ledger-driven (see
    ``promotion-status``).
  * Fail-open everywhere — an unparseable file, an unresolvable target, a git
    error, or any internal exception → silent, never a false fire, never a wedge.

CLI subcommands (one script owns all of them):
  check            — scan staged files (or ``--paths``), append ledger rows, print
                     the WARN (mode-scoped; block only when HC8_GATE_MODE=="block"
                     AND findings). Pre-commit hook entrypoint.
  backfill         — post-commit hook: bind ``commit_sha`` onto null rows whose
                     ``staged_tree_sha == HEAD^{tree}`` (EXACT tree match, never
                     "most-recent null row").
  adjudicate       — operator marks a flag_id true/false positive (append-only).
  promotion-status — deterministic pure readiness computation over the ledger.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Mode + promotion constants (the SINGLE owner of each).
# ---------------------------------------------------------------------------

# Broader classes remain WARN-only until `promotion-status` reports ready: true
# and the operator promotes them. Confirmed external sends are an explicit,
# narrower blocking exception below.
HC8_GATE_MODE = "warn"  # "warn" | "block"

HC8_GATE_SOAK_MIN_COMMITS = 20
HC8_GATE_SOAK_MIN_DAYS = 14
HC8_GATE_FP_CEILING = 0.05
HC8_GATE_GC_DAYS = 30  # unadjudicated null-commit_sha rows older than this are excluded

VERDICT_TRUE = "true_positive"
VERDICT_FALSE = "false_positive"
VERDICT_UNADJUDICATED = "unadjudicated"

# ---------------------------------------------------------------------------
# Ledger path (env-overridable so hermetic tests never touch production state).
# ---------------------------------------------------------------------------


def ledger_path() -> Path:
    override = os.environ.get("HC8_GATE_LEDGER")
    if override:
        return Path(override)
    return Path.home() / ".automation-state" / "hc8-gate" / "fp-tally.jsonl"


# ---------------------------------------------------------------------------
# (d) Protected-reference-path registry — the enumerated literal set.
# Kept in lockstep with the architecture record's "## (d) Protected-reference-path
# registry" table. A write SINK whose target is a LITERAL matching one of these
# globs fires (d); a variable-resolved target fails open. $HOME/~ normalized.
# The sanctioned autonomous writers (workspace_roadmap_commit.py, the
# consolidate-memory lane) route through named adapters, not raw sinks, so they
# are exempt by construction.
# ---------------------------------------------------------------------------

PROTECTED_REFERENCE_GLOBS: tuple[str, ...] = (
    "~/.claude/projects/*/memory/MEMORY.md",
    "~/.claude/projects/*/memory/*.md",
    "~/.codex/memories/MEMORY.md",
    "~/.codex/memories/*.md",
    "~/projects/DECISIONS.md",
    "~/projects/AGENT_OPERATING_CONTRACT.md",
    "~/projects/CHARTER.md",
    "~/.claude/CLAUDE.md",
    "~/.codex/AGENTS.md",
    "~/.openclaw/*/SOUL.md",
    "~/.openclaw/*/*/SOUL.md",
    "~/projects/ALLOWLIST.md",
    "~/projects/EXCEPTIONS.md",
    "~/projects/SURFACES.md",
    "~/projects/ALWAYS_ON.md",
    "~/projects/KNOWN_CHRONIC.md",
    "~/projects/*/ROADMAP.md",
    "~/projects/*/backlog.md",
    "~/projects/*/docs/backlog.md",
    "~/.automation-state/*/*.jsonl",
    "~/.automation-state/*/*.sqlite",
)


def _normalize_target(literal: str) -> str:
    """Normalize a path literal to a ~-rooted comparable form.

    Absolute paths under $HOME collapse back to ~/…; ~-prefixed and relative
    literals pass through. Never touches the filesystem.
    """
    home = str(Path.home())
    s = literal
    if s.startswith(home + "/"):
        s = "~/" + s[len(home) + 1 :]
    elif s == home:
        s = "~"
    return s


def _matches_protected(literal: str) -> bool:
    norm = _normalize_target(literal)
    return any(fnmatch.fnmatchcase(norm, glob) for glob in PROTECTED_REFERENCE_GLOBS)


# ---------------------------------------------------------------------------
# Write-tool taxonomy — read LIVE from registry_enforcement (no hand copy).
# Fail-open: if the import is unavailable, the taxonomy-dependent rows simply
# match nothing (the detector degrades to silent, never a false fire).
# ---------------------------------------------------------------------------


def _load_mcp_tool_patterns() -> list[re.Pattern[str]]:
    try:
        _here = Path(__file__).resolve().parent
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        import registry_enforcement as _re_mod  # type: ignore

        return list(_re_mod.MCP_MUTATING_TOOLS.values())
    except Exception:
        return []


_MCP_TOOL_PATTERNS = _load_mcp_tool_patterns()


def _is_known_write_tool(name: str) -> bool:
    return any(p.match(name) for p in _MCP_TOOL_PATTERNS)


# Per-criterion name families (matched on the LAST dotted segment, case-insensitive).
# These are the statically-decidable shapes; every runtime nuance fails open.
_BULK_SUBSTR = ("_bulk", "bulk_", "batch", "_many", "multiple_", "_rows", "_row_s")
_DESTROY_SUBSTR = (
    "delete",
    "destroy",
    "hard_delete",
    "empty_trash",
    "purge",
    "overwrite",
    "remove_file",
    "remove_permission",
    "clear_",
)
_IDENTITY_MUTATE_SUBSTR = ("rename_", "reassign", "change_key", "set_identifier")
_EXTERNAL_IRREVERSIBLE_SUBSTR = (
    "send",
    "submit",
    "purchase",
    "checkout",
    "cancel",
    "transfer",
    "create_event",
    "delete_event",
)
_DDL_KEYWORDS = (
    "ALTER TABLE",
    "DROP TABLE",
    "CREATE TABLE",
    "DROP INDEX",
    "TRUNCATE",
    "ALTER COLUMN",
    "RENAME TABLE",
)

# The gate's own source + its test carry every hunted pattern as data.
_SELF_EXEMPT_BASENAMES = frozenset(
    {"validate_consequential_write.py", "test_validate_consequential_write.py"}
)

_WRITE_SINK_NAMES = (
    "write_text",
    "write_bytes",
    "open",
    "replace",
    "copy",
    "copy2",
    "copyfile",
    "dump",
)

_PROCESS_RUNNERS = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.popen",
        "os.system",
        "os.popen",
    }
)


def _last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower()


def _any_substr(name: str, substrs: tuple[str, ...]) -> bool:
    low = _last_segment(name)
    return any(s in low for s in substrs)


# HC8 criterion -> ledger flagged_class token.
CLASS_A_BULK = "hc8_a_bulk"
CLASS_B_DESTROY = "hc8_b_unrecoverable"
CLASS_C_SCHEMA = "hc8_c_schema_identifier"
CLASS_D_REFERENCE = "hc8_d_reference_rewrite"
CLASS_E_EXTERNAL = "hc8_e_external_irreversible"
CLASS_E_EXTERNAL_ADVISORY = "hc8_e_external_advisory"

# The operator selected immediate enforcement for the only currently confirmed
# consequential action class. Keeping this set separate from HC8_GATE_MODE
# avoids promoting noisy advisory classes before their findings are reviewable.
HC8_BLOCKING_CLASSES = frozenset({CLASS_E_EXTERNAL})


@dataclass(frozen=True)
class Finding:
    flagged_class: str
    path: str
    line: int
    reason: str


# ---------------------------------------------------------------------------
# Suppression marker (sibling of # tcc-ok: / # proxy-verdict-ok: family).
# ---------------------------------------------------------------------------

_SUPPRESS_RE = re.compile(r"#\s*hc8-gate-ok:")


def _suppressed_lines(text: str, lang: str) -> set[int]:
    """1-indexed lines carrying a real (non-string-literal) # hc8-gate-ok marker.

    Python: tokenizer-checked so a string-literal marker never false-suppresses.
    Shell: comment-tail match.
    """
    out: set[int] = set()
    if lang == "python":
        try:
            import io
            import tokenize

            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT and _SUPPRESS_RE.search(tok.string):
                    out.add(tok.start[0])
        except Exception:
            # Fall back to a line scan (fail toward NOT suppressing on error).
            for i, ln in enumerate(text.splitlines(), 1):
                if _SUPPRESS_RE.search(ln) and "#" in ln:
                    out.add(i)
    else:
        for i, ln in enumerate(text.splitlines(), 1):
            # crude: marker must be in a comment position (after a #)
            if _SUPPRESS_RE.search(ln):
                out.add(i)
    return out


# ---------------------------------------------------------------------------
# Python scanner.
# ---------------------------------------------------------------------------


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        # dotted: reconstruct best-effort a.b.c
        parts = [f.attr]
        cur = f.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _string_literals(node: ast.AST) -> list[str]:
    out: list[str] = []
    for arg in ast.walk(node):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append(arg.value)
    return out


def _direct_string_args(node: ast.Call) -> list[str]:
    out: list[str] = []
    for a in list(node.args) + [kw.value for kw in node.keywords]:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append(a.value)
        # Path("lit") / Path("lit").write_text — pull the inner literal
        elif isinstance(a, ast.Call):
            for inner in a.args:
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    out.append(inner.value)
    return out


def _has_literal_command_prefix(node: ast.AST, prefix: tuple[str, ...]) -> bool:
    """Return true only when a literal process command starts with ``prefix``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            # Keep shell separators separate from command words. Plain
            # ``shlex.split`` keeps `send;curl` as one token and would miss
            # a confirmed send followed by another shell command.
            lexer = shlex.shlex(node.value, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            return tuple(lexer)[: len(prefix)] == prefix
        except ValueError:
            return False
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    if len(node.elts) < len(prefix):
        return False
    for element, expected in zip(node.elts, prefix):
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return False
        if element.value != expected:
            return False
    return True


def _process_runner_aliases(tree: ast.AST) -> dict[str, str]:
    """Resolve the bounded import forms that can invoke a process runner."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"subprocess", "os"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "os"}:
            for alias in node.names:
                candidate = f"{node.module}.{alias.name.lower()}"
                if candidate in _PROCESS_RUNNERS:
                    aliases[alias.asname or alias.name] = candidate
    return aliases


def _canonical_process_runner_name(
    node: ast.Call, aliases: dict[str, str]
) -> str | None:
    """Return a known process runner after resolving simple Python imports."""
    raw_name = _call_name(node)
    if not raw_name:
        return None
    parts = raw_name.split(".")
    first = aliases.get(parts[0], parts[0])
    candidate = ".".join([first, *parts[1:]]) if len(parts) > 1 else first
    candidate = candidate.lower()
    return candidate if candidate in _PROCESS_RUNNERS else None


def _is_exact_openclaw_send(node: ast.Call, runner_name: str | None) -> bool:
    """Recognize only a literal OpenClaw external-message command.

    Function names such as ``send_convergence_alert`` describe intent, not an
    executed consequential action. A process call with the exact command prefix
    is the static proof this gate needs before it emits the blocking class.
    """
    if runner_name is None:
        return False
    command_node = node.args[0] if node.args else next(
        (keyword.value for keyword in node.keywords if keyword.arg == "args"), None
    )
    if command_node is None:
        return False
    return _has_literal_command_prefix(command_node, ("openclaw", "message", "send"))


def _is_write_sink_call(node: ast.Call, name: str) -> bool:
    """Return true only for a statically provable write sink.

    ``open(path)`` defaults to read mode, so it is not enough to see its name.
    The other enumerated sinks are write-only APIs by contract.
    """
    seg = _last_segment(name)
    if seg not in _WRITE_SINK_NAMES:
        return False
    if seg != "open":
        return True
    for arg in list(node.args[1:]) + [kw.value for kw in node.keywords if kw.arg == "mode"]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return any(mode in arg.value for mode in ("w", "a", "x", "+"))
    return False


def _function_has_write_sink(function: ast.AST) -> bool:
    """Bounded same-file proof: the named local bulk helper writes directly."""
    pending = list(getattr(function, "body", []))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if not isinstance(node, ast.Call):
            pending.extend(ast.iter_child_nodes(node))
        else:
            name = _call_name(node)
            if name and _is_write_sink_call(node, name):
                return True
            pending.extend(ast.iter_child_nodes(node))
    return False


def scan_python(path: str, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []  # fail open
    suppressed = _suppressed_lines(text, "python")
    findings: list[Finding] = []
    local_bulk_writers = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _any_substr(node.name, _BULK_SUBSTR)
        and _function_has_write_sink(node)
    }
    process_runner_aliases = _process_runner_aliases(tree)

    # Precompute the set of nodes lexically inside a loop, for (a).
    loop_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            for child in ast.walk(node):
                loop_nodes.add(id(child))
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for child in ast.walk(node):
                loop_nodes.add(id(child))

    for node in ast.walk(tree):
        # (c) DDL string literals — any Constant str with a DDL leading token.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            head = node.value.strip().upper()
            if any(head.startswith(k) for k in _DDL_KEYWORDS):
                line = getattr(node, "lineno", 0)
                if line not in suppressed:
                    findings.append(
                        Finding(CLASS_C_SCHEMA, path, line, f"DDL literal: {head[:24]!r}")
                    )
            continue

        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if not name:
            continue
        line = getattr(node, "lineno", 0)
        if line in suppressed:
            continue
        seg = _last_segment(name)

        # (b) unrecoverable delete / overwrite.
        if _any_substr(name, _DESTROY_SUBSTR):
            findings.append(Finding(CLASS_B_DESTROY, path, line, f"destroy call: {seg}"))
            continue
        # (e) Exact literal sends are confirmed actions and may block. Broader
        # action-family names remain warning-only because their names alone do
        # not prove an external action happened.
        if _is_exact_openclaw_send(
            node, _canonical_process_runner_name(node, process_runner_aliases)
        ):
            findings.append(
                Finding(CLASS_E_EXTERNAL, path, line, "external send: openclaw message send")
            )
            continue
        # (c) record-identifier mutation.
        if _any_substr(name, _IDENTITY_MUTATE_SUBSTR):
            findings.append(
                Finding(CLASS_C_SCHEMA, path, line, f"identifier-mutation call: {seg}")
            )
            continue
        # Broader action-family names remain warning-only because their names
        # alone do not prove an external action happened. Check them after the
        # stricter classes so an overlapping name cannot downgrade a finding.
        if _any_substr(name, _EXTERNAL_IRREVERSIBLE_SUBSTR):
            findings.append(
                Finding(
                    CLASS_E_EXTERNAL_ADVISORY,
                    path,
                    line,
                    f"possible external decision: {seg}",
                )
            )
            continue
        # (a) bulk: a known bulk tool, or a same-file bulk helper that directly
        # calls a write sink. Names alone are not evidence of a write.
        if _any_substr(name, _BULK_SUBSTR) and (
            _is_known_write_tool(seg) or seg in local_bulk_writers
        ):
            findings.append(Finding(CLASS_A_BULK, path, line, f"bulk write call: {seg}"))
            continue
        if _is_known_write_tool(seg) and id(node) in loop_nodes:
            findings.append(
                Finding(CLASS_A_BULK, path, line, f"write tool in loop: {seg}")
            )
            continue
        # (d) reference rewrite: a write SINK whose direct literal arg — or, for
        # a method sink like Path("lit").write_text(...), the callee receiver's
        # literal — is a protected path.
        if _is_write_sink_call(node, name):
            lits = list(_direct_string_args(node))
            if isinstance(node.func, ast.Attribute):
                lits.extend(_string_literals(node.func.value))
            for lit in lits:
                if _matches_protected(lit):
                    findings.append(
                        Finding(
                            CLASS_D_REFERENCE,
                            path,
                            line,
                            f"reference rewrite via {seg}: {lit}",
                        )
                    )
                    break

    return findings


# ---------------------------------------------------------------------------
# Shell scanner (bounded, command-position; the Python scanner is primary).
# ---------------------------------------------------------------------------

_SH_RM_RF_RE = re.compile(r"(^|[;&|]\s*|\bthen\b\s*)rm\s+(-[a-zA-Z]*r[a-zA-Z]*)\s")
_SH_DDL_RE = re.compile(r"\b(sqlite3|psql)\b.*\b(ALTER|DROP|CREATE TABLE|TRUNCATE)\b")
_SH_REDIRECT_RE = re.compile(r"(?:>>?|tee\s+(?:-a\s+)?)\s*([^\s;|&]+)")


def scan_shell(path: str, text: str) -> list[Finding]:
    suppressed = _suppressed_lines(text, "shell")
    findings: list[Finding] = []
    for i, raw in enumerate(text.splitlines(), 1):
        if i in suppressed:
            continue
        line = raw.split("#", 1)[0]  # strip comment tail
        if _SH_RM_RF_RE.search(line):
            findings.append(Finding(CLASS_B_DESTROY, path, i, "shell rm -rf"))
        if _SH_DDL_RE.search(line):
            findings.append(Finding(CLASS_C_SCHEMA, path, i, "shell DDL"))
        for m in _SH_REDIRECT_RE.finditer(line):
            target = m.group(1).strip("\"'")
            if _matches_protected(target):
                findings.append(
                    Finding(CLASS_D_REFERENCE, path, i, f"shell redirect to protected: {target}")
                )
    return findings


def scan_file(abs_path: Path, rel_path: str) -> list[Finding]:
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []  # fail open
    # test/fixture files carry these shapes as data — exempt. So does this gate's
    # OWN source (its _DDL_KEYWORDS / name-family tuples ARE the hunted patterns as
    # data) — the AUDIT_INFRA_SELF_EXEMPT class, sibling of daily-code-audit's.
    base = abs_path.name
    if (
        base.startswith("test_")
        or base.endswith("_test.py")
        or base in _SELF_EXEMPT_BASENAMES
    ):
        return []
    if abs_path.suffix == ".py":
        return scan_python(rel_path, text)
    if abs_path.suffix in (".sh", ".bash", ".zsh"):
        return scan_shell(rel_path, text)
    return []


def scan_paths(root: Path, rel_paths: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for rel in rel_paths:
        p = (root / rel).resolve()
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        out.extend(scan_file(p, rel))
    return out


# ---------------------------------------------------------------------------
# git helpers (fail-open).
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _staged_tree_sha(root: Path) -> str | None:
    out = _git(root, "write-tree")
    return out.strip() if out else None


def _staged_paths(root: Path) -> list[str]:
    out = _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Ledger read / append (append-only).
# ---------------------------------------------------------------------------


def _append_row(row: dict) -> None:
    path = ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass  # fail open — never wedge a commit on a ledger IO error


def _read_rows() -> list[dict]:
    path = ledger_path()
    rows: list[dict] = []
    try:
        if not path.exists():
            return []
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue  # malformed row skipped (fail-open)
            if isinstance(obj, dict):
                rows.append(obj)
    except Exception:
        return []
    return rows


def compute_flag_id(staged_tree_sha: str, finding: Finding, flagged_at: str) -> str:
    """Give each independently adjudicable finding a stable event identifier."""
    material = "\0".join(
        (
            staged_tree_sha,
            finding.flagged_class,
            finding.path,
            str(finding.line),
            finding.reason,
            flagged_at,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Registry routing (reuse the existing consumer wiring).
# ---------------------------------------------------------------------------


def _registry_note() -> str:
    """Best-effort: confirm the registry readers are importable so the WARN can
    tell the operator the routing target exists. Fail-open to a generic note."""
    try:
        import registry_enforcement as _re_mod  # type: ignore

        _re_mod.load_exceptions()
        _re_mod.load_allowlist()
        return (
            "route: add the matching EXCEPTIONS.md machine-block row (or ALLOWLIST.md "
            "service row) via wsroot/wsship + operator review, or suppress the verified-"
            "safe line with `# hc8-gate-ok: <reason>`."
        )
    except Exception:
        return "route: add the matching registry row or suppress with `# hc8-gate-ok: <reason>`."


# ---------------------------------------------------------------------------
# Subcommand: check
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    now = args.now or datetime.now(timezone.utc).isoformat()

    if args.paths:
        rel_paths = list(args.paths)
        staged_tree = args.staged_tree_sha or "no-tree"
    else:
        rel_paths = _staged_paths(root)
        staged_tree = args.staged_tree_sha or _staged_tree_sha(root) or "no-tree"

    findings = scan_paths(root, rel_paths)
    blocked_classes = {
        finding.flagged_class
        for finding in findings
        if HC8_GATE_MODE == "block" or finding.flagged_class in HC8_BLOCKING_CLASSES
    }

    # One ledger row per independently adjudicable finding. Explicit --paths
    # inspection without a real tree is read-only so it cannot skew the soak.
    unique_findings = sorted(set(findings), key=lambda f: (f.path, f.line, f.reason, f.flagged_class))
    flag_ids = {finding: compute_flag_id(staged_tree, finding, now) for finding in unique_findings}
    ledger_written = not args.no_ledger and staged_tree != "no-tree"
    if ledger_written:
        for finding in unique_findings:
            _append_row(
                {
                    "flag_id": flag_ids[finding],
                    "staged_tree_sha": staged_tree,
                    "commit_sha": None,
                    "flagged_class": finding.flagged_class,
                    "path": finding.path,
                    "line": finding.line,
                    "reason": finding.reason,
                    "flagged_at": now,
                    "operator_verdict": VERDICT_UNADJUDICATED,
                }
            )

    if args.json:
        print(
            json.dumps(
                {
                    "mode": HC8_GATE_MODE,
                    "blocking_classes": sorted(HC8_BLOCKING_CLASSES),
                    "blocked_classes": sorted(blocked_classes),
                    "staged_tree_sha": staged_tree,
                    "ledger_written": ledger_written,
                    "findings": [
                        {
                            "flagged_class": f.flagged_class,
                            "path": f.path,
                            "line": f.line,
                            "reason": f.reason,
                            "flag_id": flag_ids.get(f),
                        }
                        for f in findings
                    ],
                },
                indent=2,
            )
        )
    elif findings:
        print("HC8 consequential-write gate — findings:", file=sys.stderr)
        for f in findings:
            print(
                f"  [{f.flagged_class}] {f.path}:{f.line} — {f.reason}", file=sys.stderr
            )
        print(f"  {_registry_note()}", file=sys.stderr)
        print(
            f"  ledger: {ledger_path()} (flag_ids: "
            + ", ".join(flag_ids.values())
            + ")",
            file=sys.stderr,
        )
        if not ledger_written:
            print("  ledger: not written (inspection has no real staged tree or --no-ledger).", file=sys.stderr)
        if blocked_classes:
            print(
                "  BLOCKED — remove the external-send path or add a justified suppression before committing.",
                file=sys.stderr,
            )
        else:
            print(
                "  MODE=warn — nudge only, commit proceeds. Adjudicate with "
                "`validate_consequential_write.py adjudicate <flag_id> "
                "--true-positive|--false-positive`.",
                file=sys.stderr,
            )

    if blocked_classes:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Subcommand: backfill (post-commit) — EXACT tree binding.
# ---------------------------------------------------------------------------


def cmd_backfill(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    head_tree = args.head_tree_sha or (_git(root, "rev-parse", "HEAD^{tree}") or "").strip()
    head_sha = args.head_sha or (_git(root, "rev-parse", "HEAD") or "").strip()
    if not head_tree or not head_sha:
        return 0  # fail open — nothing to bind

    rows = _read_rows()
    # Only null-commit_sha rows whose staged_tree_sha == this commit's tree.
    matched = [
        r
        for r in rows
        if r.get("commit_sha") is None and r.get("staged_tree_sha") == head_tree
    ]
    n = 0
    for r in matched:
        # append a backfill row carrying the same flag_id + the real commit_sha
        _append_row(
            {
                "flag_id": r.get("flag_id"),
                "staged_tree_sha": head_tree,
                "commit_sha": head_sha,
                "flagged_class": r.get("flagged_class"),
                "flagged_at": r.get("flagged_at"),
                "operator_verdict": r.get("operator_verdict", VERDICT_UNADJUDICATED),
            }
        )
        n += 1
    if args.json:
        print(json.dumps({"backfilled": n, "head_tree": head_tree, "head_sha": head_sha}))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: adjudicate
# ---------------------------------------------------------------------------


def cmd_adjudicate(args: argparse.Namespace) -> int:
    rows = _read_rows()
    match = [r for r in rows if r.get("flag_id") == args.flag_id]
    if not match:
        print(f"unknown flag_id: {args.flag_id}", file=sys.stderr)
        return 2
    verdict = VERDICT_FALSE if args.false_positive else VERDICT_TRUE
    latest = _latest_by_flag(rows).get(args.flag_id, match[-1])
    _append_row(
        {
            "flag_id": args.flag_id,
            "staged_tree_sha": latest.get("staged_tree_sha"),
            "commit_sha": latest.get("commit_sha"),
            "flagged_class": latest.get("flagged_class"),
            "flagged_at": latest.get("flagged_at"),
            "operator_verdict": verdict,
        }
    )
    if args.json:
        print(json.dumps({"flag_id": args.flag_id, "operator_verdict": verdict}))
    else:
        print(f"adjudicated {args.flag_id}: {verdict}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: promotion-status (pure readiness computation).
# ---------------------------------------------------------------------------


def _latest_by_flag(rows: list[dict]) -> dict[str, dict]:
    """Logically one row per flag_id, latest adjudication winning. A backfill row
    carries the commit_sha; the latest row by list order wins per field, but the
    operator_verdict of the newest ADJUDICATED row is authoritative."""
    out: dict[str, dict] = {}
    for r in rows:
        fid = r.get("flag_id")
        if not isinstance(fid, str):
            continue
        cur = out.get(fid)
        if cur is None:
            out[fid] = dict(r)
            continue
        merged = dict(cur)
        # commit_sha: keep any non-null (backfill binds it).
        if merged.get("commit_sha") is None and r.get("commit_sha") is not None:
            merged["commit_sha"] = r.get("commit_sha")
        # operator_verdict: latest non-unadjudicated wins; else keep.
        if r.get("operator_verdict") not in (None, VERDICT_UNADJUDICATED):
            merged["operator_verdict"] = r.get("operator_verdict")
        out[fid] = merged
    return out


def compute_promotion_status(rows: list[dict], now: datetime) -> dict:
    """Deterministic pure readiness computation.

    ready := adjudicated_count >= SOAK_MIN_COMMITS
             AND days_since_first_flag >= SOAK_MIN_DAYS
             AND false_positive_count / adjudicated_count <= FP_CEILING

    GC (logical, not a rewrite): unadjudicated null-commit_sha rows older than
    GC_DAYS are excluded from every count. A null-commit_sha row is never
    adjudicated-counted regardless of age (it may be an aborted/--no-verify flag).
    """
    latest = _latest_by_flag(rows)

    def _parse(ts) -> datetime | None:
        if not isinstance(ts, str):
            return None
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    flag_times: list[datetime] = []
    adjudicated = 0
    false_positives = 0
    gc_excluded = 0

    for r in latest.values():
        verdict = r.get("operator_verdict", VERDICT_UNADJUDICATED)
        committed = r.get("commit_sha") is not None
        ts = _parse(r.get("flagged_at"))
        if ts is not None:
            flag_times.append(ts)

        if verdict == VERDICT_UNADJUDICATED:
            # GC: stale unadjudicated null-commit_sha rows are excluded.
            if not committed and ts is not None:
                age_days = (now - ts).total_seconds() / 86400.0
                if age_days > HC8_GATE_GC_DAYS:
                    gc_excluded += 1
            continue

        # Adjudicated rows count regardless of commit_sha binding.
        adjudicated += 1
        if verdict == VERDICT_FALSE:
            false_positives += 1

    days_since_first = 0.0
    if flag_times:
        days_since_first = (now - min(flag_times)).total_seconds() / 86400.0

    fp_rate = (false_positives / adjudicated) if adjudicated else 0.0
    ready = bool(
        adjudicated >= HC8_GATE_SOAK_MIN_COMMITS
        and days_since_first >= HC8_GATE_SOAK_MIN_DAYS
        and fp_rate <= HC8_GATE_FP_CEILING
    )
    return {
        "ready": ready,
        "mode": HC8_GATE_MODE,
        "adjudicated_count": adjudicated,
        "false_positive_count": false_positives,
        "fp_rate": round(fp_rate, 4),
        "days_since_first_flag": round(days_since_first, 2),
        "gc_excluded_stale_null_rows": gc_excluded,
        "thresholds": {
            "soak_min_commits": HC8_GATE_SOAK_MIN_COMMITS,
            "soak_min_days": HC8_GATE_SOAK_MIN_DAYS,
            "fp_ceiling": HC8_GATE_FP_CEILING,
            "gc_days": HC8_GATE_GC_DAYS,
        },
    }


def cmd_promotion_status(args: argparse.Namespace) -> int:
    now = (
        datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if args.now
        else datetime.now(timezone.utc)
    )
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    status = compute_promotion_status(_read_rows(), now)
    print(json.dumps(status, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HC8 consequential-write pre-ship gate.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="scan staged files (or --paths) and record independently adjudicable findings")
    c.add_argument("--root", help="repo root (default cwd)")
    c.add_argument("--paths", nargs="*", help="explicit rel paths to scan (test/CI)")
    c.add_argument("--staged-tree-sha", help="override staged tree sha (test)")
    c.add_argument("--now", help="override flagged_at ISO timestamp (test)")
    c.add_argument("--no-ledger", action="store_true", help="inspect without appending ledger rows")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("backfill", help="post-commit: bind commit_sha by exact tree match")
    b.add_argument("--root", help="repo root (default cwd)")
    b.add_argument("--head-tree-sha", help="override HEAD^{tree} (test)")
    b.add_argument("--head-sha", help="override HEAD sha (test)")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_backfill)

    a = sub.add_parser("adjudicate", help="operator marks a flag_id true/false positive")
    a.add_argument("flag_id")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--false-positive", action="store_true")
    g.add_argument("--true-positive", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_adjudicate)

    s = sub.add_parser("promotion-status", help="deterministic readiness over the ledger")
    s.add_argument("--now", help="override now ISO timestamp (test)")
    s.set_defaults(func=cmd_promotion_status)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # fail-open: never wedge a commit on an internal error
        print(f"hc8-gate: internal error (fail-open): {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
