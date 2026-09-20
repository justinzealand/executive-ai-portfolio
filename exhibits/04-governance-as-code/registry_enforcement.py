#!/usr/bin/env python3
# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Runtime enforcement of workspace registries — ALLOWLIST / EXCEPTIONS / ALWAYS_ON.

Phase 1 of the charter B2 mandatory-consumer runtime binding
(`docs/plans/registry-runtime-enforcement.md`).

Both the CC dispatcher (`<workspace>/claude-code-hooks/dispatcher.py`) and the
Codex dispatcher (`~/.codex/hooks/dispatcher.py`) import this module via
``sys.path.insert(0, <workspace>/scripts)`` and call ``evaluate_bash`` /
``evaluate_mcp`` from the PreToolUse hook. The dispatchers wrap the returned
``EvalResult`` into their runtime's deny-JSON on ``outcome=block``; on any
non-``allow`` outcome they emit ``render_stderr_line`` to their own stderr for
audit-trail visibility.

Design invariants (from the plan's Frozen decisions — do not edit these without
a charter change):

- **Fail-OPEN on classifier ambiguity or error, fail-LOUD only on confirmed
  violation.** A guard bug never wedges a session. `evaluate_bash` /
  `evaluate_mcp` always return an ``EvalResult`` (never raise).
- **Write-only** for Bash: the classifier requires a positive write-intent
  signal via ``is_bash_write``. Bare-GET reads to unknown hosts NEVER block
  (charter S6 C5 one-off-read exclusion, honored deterministically).
- **MCP mutating-only**: the classifier's ``MCP_MUTATING_TOOLS`` mapping
  enumerates the exact write tool names per family. Read-only tool names never
  enter the classifier at all.
- **ALWAYS_ON CREATE/START blocks; STOP/REMOVE never blocks** so cleanup of
  unregistered services stays possible.
- **Two-registry routing** for MCP: iOS linchpins → EXCEPTIONS only (no
  ALLOWLIST — Apple is local); ``google_calendar_write`` → BOTH ALLOWLIST
  (Google row) AND EXCEPTIONS (``google_calendar_write`` machine-block entry);
  every other write class → ALLOWLIST (service row) only.
- **Bypass**: ``# registry-ok: <reason>`` on the same Bash-command line
  short-circuits the block with ``outcome=allow_bypassed``; the reason is
  captured verbatim in the stderr line.

Called-out reference points in the codebase:

- ``<workspace>/scripts/repo_guard.py`` — the read-only-anchor guard this
  module mirrors architecturally.
- ``<workspace>/ALLOWLIST.md`` / ``EXCEPTIONS.md`` / ``ALWAYS_ON.md`` — the
  three registry files parsed at call time.
"""
from __future__ import annotations
import os

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable
from urllib.parse import urlparse


WORKSPACE_ROOT = Path(
    os.environ.get("WORKSPACE_ROOT", str(Path.home() / "projects"))
)
ALLOWLIST_PATH = WORKSPACE_ROOT / "ALLOWLIST.md"
EXCEPTIONS_PATH = WORKSPACE_ROOT / "EXCEPTIONS.md"
ALWAYS_ON_PATH = WORKSPACE_ROOT / "ALWAYS_ON.md"

MACHINE_BLOCK_START_RE = re.compile(
    r"^```yaml\s+# machine-parseable exceptions.*$",
    re.IGNORECASE | re.MULTILINE,
)


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AllowlistSnapshot:
    """Parsed ALLOWLIST.md — service name → attributes.

    Keys are lower-cased service names (``"slack"``, ``"gmail"``, ``"github"``).
    Values are the raw ``{auth, scope, reason}`` strings (used only for message
    rendering, not classification).
    """

    services: dict[str, dict[str, str]] = field(default_factory=dict)
    # host token (lower) → canonical service key (for URL host lookup)
    host_index: dict[str, str] = field(default_factory=dict)
    parse_error: str | None = None


@dataclass(frozen=True)
class ExceptionsSnapshot:
    """Parsed EXCEPTIONS.md machine-parseable block.

    ``entries`` is the runtime-tool-class index used by
    ``evaluate_bash``/``evaluate_mcp`` for default-deny classification —
    entries with ``runtime_tool_class == "n/a — not a runtime tool-call
    class"`` are filtered OUT here (they never gate a live tool call).

    ``all_entries`` preserves EVERY parsed entry — including the non-runtime
    ones — so a consumer that needs a specific ``class_id`` (like the
    ``agent_auto_merge_code_review`` sign-off block consumed by the
    agent-review-gate pre-merge check) can find it.
    """

    entries: dict[str, dict[str, str]] = field(default_factory=dict)
    all_entries: list[dict[str, str]] = field(default_factory=list)
    parse_error: str | None = None


@dataclass(frozen=True)
class SignoffBlock:
    """The ``agent_auto_merge_code_review`` sign-off entry from EXCEPTIONS.md.

    Ships in phase-2 PR #3 as part of the cross-agent code-review gate.
    Optional v2 fields (``approver``, ``signoff_commit_sha``,
    ``signoff_pr_number``) carry the GitHub-approval provenance the phase-2
    PR #5 verifier binds to. The phase-2 EXCEPTIONS.md entry is
    operator-authored — never written by an agent — so absence of the block
    is a distinct outcome (``load_agent_review_gate_signoff() -> None``) from
    a present-but-unverifiable one.
    """

    class_id: str
    scope: str
    granted_date: str
    review_condition: str
    runtime_tool_class: str
    approver: str | None = None
    signoff_commit_sha: str | None = None
    signoff_pr_number: str | None = None
    # H1/H3/H5 lane-scope envelope (agent-dispatch rung-3). All optional and
    # default-absent, so a block carrying none of them verifies exactly as
    # today for the shared consumers (autonomous_landing_recovery,
    # ship_merge_wrapper, daily-health-check). Lane-scope ENFORCEMENT lives in
    # agent_dispatch_lane.verify_lane_scope, NOT in the shared check_signoff
    # contract — see docs/architecture/records/agent-dispatch-activation.md.
    lane_id: str | None = None
    target_repo: str | None = None
    base_branch: str | None = None
    fence_globs: tuple[str, ...] = ()
    permitted_item_classes: tuple[str, ...] = ()
    merge_method: str | None = None
    actor: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class AlwaysOnSnapshot:
    """Parsed ALWAYS_ON.md — set of registered plist labels."""

    labels: set[str] = field(default_factory=set)
    parse_error: str | None = None


@dataclass(frozen=True)
class EvalResult:
    """Outcome of a single ``evaluate_bash``/``evaluate_mcp`` call.

    ``outcome`` is one of:
      - ``allow`` — the common path; classifier saw nothing to gate.
      - ``allow_bypassed`` — a Bash bypass token silenced a would-be block.
      - ``allow_unclassifiable`` — shell-var indirection, un-enumerated MCP
        family, or otherwise-ambiguous input — fail-OPEN by design.
      - ``allow_localhost`` — outbound target resolved to localhost/tailnet;
        never gated because it's inside the network boundary.
      - ``allow_error`` — an internal exception inside the classifier or one
        of the registry loaders; fail-OPEN so a guard bug never wedges the
        session.
      - ``block`` — confirmed violation; the dispatcher renders this as a
        PreToolUse deny.
    """

    outcome: str
    registry: str | None = None
    host_or_label: str | None = None
    row_template: str | None = None
    bypass_hint: str | None = None
    bypass_reason: str | None = None
    unclassifiable_reason: str | None = None
    error: str | None = None
    tool: str | None = None


# --------------------------------------------------------------------------
# MCP mutating-tool mapping (enumerated per family — reads NEVER appear)
# --------------------------------------------------------------------------


# Regex-per-family: the classifier matches a tool name against the family
# whose regex matches. A tool name matching NO family is unclassifiable
# (fail-open allow).
MCP_MUTATING_TOOLS: dict[str, re.Pattern[str]] = {
    "slack": re.compile(
        r"^slack_(send_message(_draft)?|schedule_message|add_reaction|"
        r"create_canvas|update_canvas)$"
    ),
    "gmail": re.compile(
        r"^gmail_(send_email|reply_to_email|create_draft(_reply)?|"
        r"delete_email|archive_email|add_label_to_email|remove_label_from_"
        r"(email|conversation)|create_label)$"
    ),
    "google_calendar": re.compile(
        r"^google_calendar_("
        r"create_(calendar|detailed_event)|"
        r"update_event|delete_event|move_event_to_another_calendar|"
        r"add_attendee_s_to_event|quick_add_event|"
        r"make_api_mutating_request)$"
    ),
    "google_docs": re.compile(
        r"^google_docs_("
        r"append_text_to_document|create_document_from_(template|text)|"
        r"find_and_replace_text|format_text|insert_image|insert_text|"
        r"make_api_mutating_request|replace_image|"
        r"update_document_properties|upload_document)$"
    ),
    "google_drive": re.compile(
        r"^google_drive_("
        r"add_file_sharing_preference|copy_file|create_(file_from_text|"
        r"folder|shared_drive|shortcut)|delete_file(_permanent)?|"
        r"make_api_mutating_request|move_file|remove_file_permission|"
        r"replace_file|update_(file_folder_name|file_or_folder_metadata)|"
        r"upload_file)$"
    ),
    "google_sheets": re.compile(
        r"^google_sheets_("
        r"change_sheet_properties|clear_spreadsheet_row_s|copy_(range|"
        r"worksheet)|create_(conditional_formatting_rule|multiple_"
        r"spreadsheet_rows|spreadsheet|spreadsheet_column|spreadsheet_row|"
        r"spreadsheet_row_at_top|worksheet)|delete_(sheet|spreadsheet_"
        r"row_s)|format_(cell_range|spreadsheet_row)|"
        r"make_api_mutating_request|rename_sheet|set_data_validation|"
        r"sort_range|update_spreadsheet_row(_s)?)$"
    ),
    "notion": re.compile(
        r"^notion[_-]("
        r"add_(block_to_page|comment|content_to_page)|archive_(data_source"
        r"_item|pages)?|create_(comment|data_source_item|database|page|"
        r"pages|view)|duplicate_page|move_pages|restore_data_source_item|"
        r"update_(data_source_item|data_source_schema|database|page|view)|"
        r"upload_file_to_(data_source_item|page_or_block)|"
        r"make_api_mutating_request)$"
    ),
    "apple_suite_notes": re.compile(
        r"^apple_suite__notes_("
        r"create_(folder|note)|delete_(folder|note)|move_note|"
        r"rename_folder|update_note)$"
    ),
    "apple_suite_reminders": re.compile(
        r"^apple_suite__reminders_("
        r"complete_reminder|create_(list|reminder)|delete_(list|reminder)|"
        r"rename_list|update_reminder)$"
    ),
    "apple_suite_calendar": re.compile(
        r"^apple_suite__calendar_("
        r"create_event|delete_event|update_event)$"
    ),
    "zoom": re.compile(
        r"^zoom_("
        r"create_(meeting|meeting_registrant|webinar_registrant)|"
        r"delete_meeting_recordings|update_zoom_meeting|"
        r"make_api_mutating_request)$"
    ),
    "todoist": re.compile(
        r"^todoist_(add_task|complete_task|reopen_task)$"
    ),
    "bear": re.compile(r"^bear_(create_note|update_note)$"),
}


# Which family routes to which registry class.
# ``exceptions_only`` — iOS linchpins (Apple is local, no ALLOWLIST row).
# ``allowlist_and_exceptions`` — Google Calendar (both required).
# ``allowlist_only`` — everything else.
_MCP_FAMILY_TO_REGISTRY_CLASS: dict[str, str] = {
    "apple_suite_notes": "exceptions_only",
    "apple_suite_reminders": "exceptions_only",
    "apple_suite_calendar": "exceptions_only",
    "google_calendar": "allowlist_and_exceptions",
    # everything else defaults to allowlist_only
}


# Family → exception class id used in EXCEPTIONS.md machine-parseable block.
_MCP_FAMILY_TO_EXCEPTION_CLASS: dict[str, str] = {
    "apple_suite_notes": "ios_notes_write",
    "apple_suite_reminders": "ios_reminders_write",
    "apple_suite_calendar": "ios_calendar_write",
    "google_calendar": "google_calendar_write",
}


# Family → service name in ALLOWLIST.md (used for allowlist lookup).
_MCP_FAMILY_TO_SERVICE_NAME: dict[str, str] = {
    "slack": "slack",
    "gmail": "gmail",
    "google_calendar": "google calendar",
    "google_docs": "google drive",  # Docs auth path is the Google Drive row today
    "google_drive": "google drive",
    "google_sheets": "google drive",
    "notion": "notion",
    "zoom": "zoom",
    "todoist": "todoist",
    "bear": "bear",
}

# Families whose MCP calls go over a LOCAL (stdio) MCP server, not the
# outbound network. Bash `curl` cannot reach these, so no `_KNOWN_HOSTS`
# entry is needed — but they still route through ALLOWLIST for the presence
# check (the operator's authorized-integration ledger).
_MCP_LOCAL_ONLY_FAMILIES: frozenset[str] = frozenset(["bear"])


# Host → canonical service name for URL lookup (used by is_bash_write path).
_KNOWN_HOSTS: dict[str, str] = {
    # Slack
    "slack.com": "slack",
    "hooks.slack.com": "slack",
    "wss-primary.slack.com": "slack",
    # Google
    "gmail.googleapis.com": "gmail",
    "smtp.gmail.com": "gmail",
    "imap.gmail.com": "gmail",
    "calendar.google.com": "google calendar",
    "calendar.googleapis.com": "google calendar",
    "www.googleapis.com": "google drive",
    "drive.google.com": "google drive",
    "docs.google.com": "google drive",
    "sheets.googleapis.com": "google drive",
    "forms.googleapis.com": "google drive",
    # GitHub
    "api.github.com": "github",
    "github.com": "github",
    "raw.githubusercontent.com": "github",
    "codeload.github.com": "github",
    # Notion
    "api.notion.com": "notion",
    "www.notion.com": "notion",
    "notion.com": "notion",
    "notion.so": "notion",
    # Telegram
    "api.telegram.org": "telegram",
    # OpenAI (Codex / ChatGPT via subscription)
    "api.openai.com": "openai",
    "chatgpt.com": "openai",
    "chat.openai.com": "openai",
    "openai.com": "openai",
    # Anthropic
    "api.anthropic.com": "anthropic",
    "claude.ai": "anthropic",
    # Todoist
    "api.todoist.com": "todoist",
    # Zoom
    "api.zoom.us": "zoom",
    "zoom.us": "zoom",
    # Granola
    "api.granola.ai": "granola",
    # Spotify
    "api.spotify.com": "spotify",
    "accounts.spotify.com": "spotify",
}


# --------------------------------------------------------------------------
# Localhost / tailnet detection (never classified — inside network boundary)
# --------------------------------------------------------------------------


_LOCALHOST_HOSTS = frozenset(
    ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
)
_TAILNET_SUFFIX = ".ts.net"
# Codex pass-2 P0-2 fix: the tailnet-only exemption excludes port :8443, which
# is the public-internet Tailscale Funnel per EXCEPTIONS.md + ALLOWLIST.md.
# Everything else on `*.ts.net` is tailnet-only per the Tailscale ACL default.
_TAILNET_PUBLIC_PORTS = frozenset([8443])


def _is_localhost_or_tailnet(host: str, port: int | None = None) -> bool:
    h = host.lower()
    if h in _LOCALHOST_HOSTS:
        return True
    if h.endswith(_TAILNET_SUFFIX):
        if port is not None and port in _TAILNET_PUBLIC_PORTS:
            return False
        return True
    return False


# --------------------------------------------------------------------------
# Bash write-intent predicate
# --------------------------------------------------------------------------


_CURL_WRITE_FLAGS = frozenset(
    [
        "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
        "-F", "--form",
        "-T", "--upload-file",
    ]
)
# `-X <method>` requires the following token; separate handling below.
_WGET_METHOD_FLAG = "--method"
_MUTATING_HTTP_METHODS = frozenset(["POST", "PUT", "PATCH", "DELETE"])

# gh subcommand groups: subcommand → set of mutating verbs
_GH_MUTATING_VERBS: dict[str, frozenset[str]] = {
    # Codex pass-2 P1 fix: `diff` (server-side render, no state change) and
    # `checkout` (local `git fetch` + branch create, no remote-side write
    # beyond fetch) are read/local. Excluded.
    "pr": frozenset(
        ["create", "edit", "comment", "close", "reopen", "merge", "review",
         "ready"]
    ),
    "issue": frozenset(
        ["create", "edit", "comment", "close", "reopen", "pin", "unpin",
         "transfer", "lock", "unlock", "develop"]
    ),
    "release": frozenset(["create", "edit", "delete", "upload"]),
    "workflow": frozenset(["run", "enable", "disable"]),
    # Codex pass-2 P1 fix: `watch` polls a run's completion — neither triggers
    # nor mutates the run. Excluded.
    "run": frozenset(["rerun", "cancel", "delete"]),
    "gist": frozenset(["create", "edit", "delete", "clone"]),
    "repo": frozenset(
        ["create", "edit", "delete", "archive", "unarchive", "fork",
         "rename", "sync", "set-default"]
    ),
    "label": frozenset(["create", "edit", "delete", "clone"]),
    "secret": frozenset(["set", "delete"]),
    "variable": frozenset(["set", "delete"]),
    "auth": frozenset(["login", "logout", "refresh", "switch", "setup-git",
                       "token"]),
}


def is_bash_write(command: str) -> bool:
    """Return True iff ``command`` carries a positive write-intent signal.

    See the plan's Frozen decision "Classifier scope #1" for the exhaustive
    predicate. Silence (bare GET, `ls`, `cat`, `git status`) → False.

    Fail-safe: any tokenization error returns False (treat as read; the
    read-side is the more-permissive direction that preserves the C5 one-off-
    read exclusion).
    """
    try:
        tokens = shlex.split(command, posix=True, comments=False)
    except Exception:
        return False
    if not tokens:
        return False

    # Strip a leading env-var-assignment prefix (`FOO=bar curl …`) — walk past
    # any `KEY=value` tokens before the first bare word.
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens):
        return False
    argv = tokens[i:]
    verb = _basename(argv[0])

    if verb in ("curl", "wget"):
        return _has_curl_wget_write_flag(argv[1:])
    if verb == "gh":
        return _gh_mutating_command_target(command)[0]
    if verb == "http":
        # HTTPie: `http POST <url>` / `http PUT <url> field=value`
        for tok in argv[1:]:
            if tok.upper() in _MUTATING_HTTP_METHODS:
                return True
        return False
    # `nc`/`ncat -c` sends a payload; a bare `nc host port` is a listener/read
    if verb in ("nc", "ncat"):
        return "-c" in argv[1:]
    # websocat — presence of `-` (stdin body) or `--text` on a URL
    if verb == "websocat":
        return True  # any invocation of websocat writes; rare enough to be permissive
    return False


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def _gh_mutating_command_target(command: str) -> tuple[bool, str | None]:
    """Return whether a `gh` command writes and any explicit target host.

    Normal `gh` writes use the configured GitHub host, so URLs in their body
    text must not be classified as destinations. Explicit ``GH_HOST``,
    ``--hostname``, and fully-qualified ``--repo``/``-R`` options change that
    assumption and must go through the allowlist like every other write.
    """
    try:
        tokens = shlex.split(command, posix=True, comments=False)
    except Exception:
        return False, None
    if not tokens:
        return False, None
    i = 0
    host: str | None = None
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        name, value = tokens[i].split("=", 1)
        if name == "GH_HOST" and value:
            host = value.lower()
        i += 1
    if i >= len(tokens):
        return False, None
    argv = tokens[i:]
    if _basename(argv[0]) != "gh":
        return False, None

    filtered: list[str] = []
    args = argv[1:]
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--hostname" and i + 1 < len(args):
            host = args[i + 1].lower()
            i += 2
            continue
        if token.startswith("--hostname="):
            host = token.split("=", 1)[1].lower()
            i += 1
            continue
        if token in ("--repo", "-R") and i + 1 < len(args):
            repo = args[i + 1]
            if repo.count("/") >= 2:
                host = repo.split("/", 1)[0].lower()
            i += 2
            continue
        if token.startswith("--repo="):
            repo = token.split("=", 1)[1]
            if repo.count("/") >= 2:
                host = repo.split("/", 1)[0].lower()
            i += 1
            continue
        filtered.append(token)
        i += 1
    return _has_gh_write_shape(filtered), host


def _is_gh_mutating_command(command: str) -> bool:
    """True iff ``command`` is a `gh` invocation matching a mutating shape."""
    return _gh_mutating_command_target(command)[0]


def _has_curl_wget_write_flag(args: Iterable[str]) -> bool:
    args = list(args)
    i = 0
    while i < len(args):
        tok = args[i]
        # -X POST style
        if tok in ("-X", "--request"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if nxt.upper() in _MUTATING_HTTP_METHODS:
                return True
            i += 2
            continue
        # --request=POST / -X=POST (uncommon but supported by getopt)
        if tok.startswith("-X=") or tok.startswith("--request="):
            val = tok.split("=", 1)[1]
            if val.upper() in _MUTATING_HTTP_METHODS:
                return True
            i += 1
            continue
        # -d / --data / -F / -T family
        if tok in _CURL_WRITE_FLAGS or _split_flag_key(tok) in _CURL_WRITE_FLAGS:
            return True
        # wget --method=POST
        if tok == _WGET_METHOD_FLAG:
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if nxt.upper() in _MUTATING_HTTP_METHODS:
                return True
            i += 2
            continue
        if tok.startswith(f"{_WGET_METHOD_FLAG}="):
            val = tok.split("=", 1)[1]
            if val.upper() in _MUTATING_HTTP_METHODS:
                return True
        i += 1
    return False


def _split_flag_key(tok: str) -> str:
    """Return the flag key portion of `--flag=value`, else the whole token."""
    if "=" in tok and tok.startswith("-"):
        return tok.split("=", 1)[0]
    return tok


def _has_gh_write_shape(args: list[str]) -> bool:
    if not args:
        return False
    sub = args[0]
    rest = args[1:]

    if sub == "api":
        # gh api requires -X <method> OR body-carrying flag to be a write.
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("-X", "--method"):
                nxt = rest[i + 1] if i + 1 < len(rest) else ""
                if nxt.upper() in _MUTATING_HTTP_METHODS or nxt.upper() == "GRAPHQL":
                    return True
                i += 2
                continue
            if tok.startswith("-X=") or tok.startswith("--method="):
                val = tok.split("=", 1)[1]
                if val.upper() in _MUTATING_HTTP_METHODS or val.upper() == "GRAPHQL":
                    return True
            if tok in ("-f", "-F", "--input", "--field", "--raw-field"):
                return True
            if tok.startswith("--field=") or tok.startswith("--raw-field="):
                return True
            i += 1
        return False

    verbs = _GH_MUTATING_VERBS.get(sub)
    if verbs is None:
        return False
    if not rest:
        return False
    # First non-flag token after `sub` is the verb.
    for tok in rest:
        if tok.startswith("-"):
            continue
        return tok in verbs
    return False


# --------------------------------------------------------------------------
# URL / host extraction for Bash outbound classification
# --------------------------------------------------------------------------


_URL_RE = re.compile(r"\b(https?://[^\s'\"`\\<>]+)")


def extract_first_url_host(command: str) -> str | None:
    """Return the lower-cased host of the first http(s) URL in the command.

    Returns None when no URL literal is present OR when the URL is a shell
    variable / heredoc — the classifier degrades to unclassifiable in that
    case (fail-open allow).
    """
    hp = extract_first_url_host_port(command)
    return hp[0] if hp else None


def extract_first_url_host_port(command: str) -> tuple[str, int | None] | None:
    """Return ``(host, port)`` for the first http(s) URL literal in ``command``.

    Port is None when the URL uses the default port for its scheme; otherwise
    the explicit port. Returns None when no URL literal is present.
    """
    hosts = extract_url_host_ports(command)
    return hosts[0] if hosts else None


def extract_url_host_ports(command: str) -> list[tuple[str, int | None]]:
    """Return every literal http(s) destination host and port in ``command``."""
    hosts: list[tuple[str, int | None]] = []
    for match in _URL_RE.finditer(command):
        try:
            parsed = urlparse(match.group(1))
            if parsed.hostname:
                hosts.append((parsed.hostname.lower(), parsed.port))
        except ValueError:
            continue
    return hosts


_SHELL_VAR_URL_RE = re.compile(
    # Heuristic — a `curl`/`wget`/`gh api` command whose URL argument is a
    # bare shell variable (unresolvable at hook time).
    r"\b(curl|wget)\s+[^\n]*\"\$[A-Za-z_][A-Za-z0-9_]*\""
)


def is_shell_var_url(command: str) -> bool:
    """True iff the command's URL argument is a shell variable (unresolvable)."""
    if _URL_RE.search(command):
        return False
    return bool(_SHELL_VAR_URL_RE.search(command))


# --------------------------------------------------------------------------
# Bypass token parsing (`# registry-ok: <reason>`)
# --------------------------------------------------------------------------


_BYPASS_RE = re.compile(r"#\s*registry-ok:\s*(.+?)\s*$", re.MULTILINE)


def extract_bypass_reason(command: str) -> str | None:
    """Return the bypass reason string, or None if no bypass token present."""
    m = _BYPASS_RE.search(command)
    if not m:
        return None
    return m.group(1).strip()


# --------------------------------------------------------------------------
# ALWAYS_ON — plist path / label extraction
# --------------------------------------------------------------------------


_LAUNCH_DIRS = (
    str(Path.home() / "Library" / "LaunchAgents") + "/",
    "/Library/LaunchDaemons/",
    "/Library/LaunchAgents/",
)

_LAUNCH_DIRS_TILDE = ("~/Library/LaunchAgents/",)


_CREATE_INVOKERS = frozenset(["cp", "mv", "install", "tee", "cat"])
_DESTROY_INVOKERS = frozenset(["rm"])


def _plist_target_from_tokens(tokens: list[str]) -> str | None:
    """Return the first token that looks like a plist under LaunchAgents/Daemons."""
    for tok in tokens:
        if tok.endswith(".plist") and _under_launch_dir(tok):
            return tok
    return None


def _under_launch_dir(path_token: str) -> bool:
    for prefix in _LAUNCH_DIRS + _LAUNCH_DIRS_TILDE:
        if prefix in path_token:
            return True
    return False


def _label_from_plist_path(path_token: str) -> str | None:
    name = path_token.rsplit("/", 1)[-1]
    if not name.endswith(".plist"):
        return None
    return name[: -len(".plist")]


def _label_from_launchctl_target(tok: str) -> str | None:
    """`gui/501/com.foo.bar` → `com.foo.bar`; `com.foo.bar` → `com.foo.bar`."""
    if "/" in tok:
        return tok.rsplit("/", 1)[-1]
    if tok.startswith("com.") or tok.startswith("org.") or tok.startswith("io."):
        return tok
    return None


def classify_launchd_command(command: str) -> tuple[str, str | None] | None:
    """Classify a launchd-related Bash command.

    Returns ``(kind, label)`` where ``kind`` is one of:

      - ``"create"`` — a CREATE/START shape targeting a plist under a launch
        dir (or a `launchctl bootstrap|load` on one).
      - ``"destroy"`` — a STOP/REMOVE shape (never blocked, but reported so
        callers can log).
      - None — not a launchd command.

    ``label`` may be None if the shape matched but the label couldn't be
    parsed (fail-open direction).
    """
    try:
        tokens = shlex.split(command, posix=True, comments=False)
    except Exception:
        return None
    if not tokens:
        return None

    # Walk past env-var assignments
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens):
        return None
    argv = tokens[i:]
    verb = _basename(argv[0])

    # launchctl subcommands (highest priority — always about a label)
    if verb == "launchctl":
        if len(argv) < 2:
            return None
        sub = argv[1]
        if sub in ("bootstrap", "load"):
            # target is either a path or a domain + path
            plist = _plist_target_from_tokens(argv[2:])
            if plist:
                return ("create", _label_from_plist_path(plist))
            return None
        if sub in ("bootout", "unload", "remove", "disable"):
            for tok in argv[2:]:
                lbl = _label_from_launchctl_target(tok)
                if lbl:
                    return ("destroy", lbl)
                plist = None
                if tok.endswith(".plist") and _under_launch_dir(tok):
                    plist = tok
                if plist:
                    return ("destroy", _label_from_plist_path(plist))
            return ("destroy", None)
        if sub == "kickstart":
            for tok in argv[2:]:
                lbl = _label_from_launchctl_target(tok)
                if lbl:
                    return ("destroy", lbl)  # kickstart -k is a restart, not create
            return ("destroy", None)
        return None

    # Redirect shapes: `cat > <plist>` / `tee <plist>` / heredoc → plist
    # `>` / `>>` isn't a shlex token; check the raw command string for the
    # redirect-plus-launch-dir combo.
    for prefix in _LAUNCH_DIRS + _LAUNCH_DIRS_TILDE:
        if prefix in command:
            for match in re.finditer(rf"[^\s]*{re.escape(prefix)}[^\s]+\.plist", command):
                path = match.group(0)
                # Distinguish create from destroy from bare-read
                if verb in _DESTROY_INVOKERS:
                    return ("destroy", _label_from_plist_path(path))
                # `cat` is special: bare `cat <path>` is a READ; `cat > path`
                # is a write. Check for the redirect BEFORE treating cat as a
                # create invoker. (Bug caught by pass-3 regression test.)
                if verb == "cat":
                    if re.search(r"[>]{1,2}", command):
                        return ("create", _label_from_plist_path(path))
                    return None
                if verb in _CREATE_INVOKERS:
                    return ("create", _label_from_plist_path(path))
                # `mv src dst` shapes
                if verb == "mv" and path in tokens:
                    return ("create", _label_from_plist_path(path))
                # rm without launch-dir prefix already handled above
    return None


# --------------------------------------------------------------------------
# Registry loaders
# --------------------------------------------------------------------------


def load_allowlist(path: Path | str = ALLOWLIST_PATH) -> AllowlistSnapshot:
    """Parse the ALLOWLIST.md "Allowed outbound integrations" markdown table.

    Fail-open on read/parse error (returns empty snapshot with ``parse_error``
    populated; the classifier then degrades to ``allow_error``).
    """
    try:
        text = Path(path).read_text()
    except Exception as e:
        return AllowlistSnapshot(parse_error=str(e))

    services: dict[str, dict[str, str]] = {}
    in_table = False
    for line in text.splitlines():
        if line.strip().startswith("|"):
            # Skip header + separator rows
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            if not in_table:
                # Header row
                if "service" in cells[0].lower():
                    in_table = True
                continue
            if all(re.fullmatch(r"[-:]+", c) for c in cells):
                continue
            # Data row: | Service | Auth | Scope | Reason |
            raw = _strip_markdown(cells[0])
            name = _normalize_service_name(raw)
            if not name:
                continue
            services[name] = {
                "raw_name": raw,
                "auth": _strip_markdown(cells[1]) if len(cells) > 1 else "",
                "scope": _strip_markdown(cells[2]) if len(cells) > 2 else "",
                "reason": _strip_markdown(cells[3]) if len(cells) > 3 else "",
            }
        else:
            if in_table and line.strip() == "":
                # End of table
                break

    # Build host_index from _KNOWN_HOSTS, filtered to services actually present
    host_index: dict[str, str] = {}
    for host, svc in _KNOWN_HOSTS.items():
        if svc.lower() in services:
            host_index[host] = svc.lower()
    return AllowlistSnapshot(services=services, host_index=host_index)


def _strip_markdown(cell: str) -> str:
    # Strip bold **X**, italic *X*, backticks
    return re.sub(r"[*`]", "", cell).strip()


def _normalize_service_name(raw: str) -> str:
    """Canonical service name for ALLOWLIST lookup.

    Strips a trailing parenthetical qualifier (`Slack (operator-handle workspace)`
    → `slack`), lowercases the result. Match keys in `_KNOWN_HOSTS` (which are
    short names) and `_MCP_FAMILY_TO_SERVICE_NAME`.
    """
    s = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip().lower()
    return s


def load_exceptions(path: Path | str = EXCEPTIONS_PATH) -> ExceptionsSnapshot:
    """Parse the "## Machine-parseable exceptions" YAML-fenced block.

    The block is expected to be a small hand-authored YAML document with a
    top-level ``version: 1`` or ``version: 2`` and an ``entries:`` list. We
    do NOT depend on PyYAML — the schema is fixed and the parser is a small
    hand-written one so the module has zero dependencies beyond the standard
    library.

    Schema v2 (added phase-2 PR #3) is a backward-compatible extension of
    v1: it adds three OPTIONAL fields on entries that require GitHub-approval
    provenance — ``approver``, ``signoff_commit_sha``, ``signoff_pr_number``.
    v1 entries continue to parse; v2 entries carry the extra fields into
    ``all_entries`` for the ``load_agent_review_gate_signoff`` accessor.

    Fail-open on any parse error.
    """
    try:
        text = Path(path).read_text()
    except Exception as e:
        return ExceptionsSnapshot(parse_error=str(e))

    # Extract YAML block that follows the "## Machine-parseable exceptions"
    # heading. Both a YAML fence header (```yaml or ```yaml # ...) and a
    # plain markdown code fence right below the heading are accepted.
    m = re.search(
        r"^##\s+Machine-parseable exceptions.*?\n"
        r"(?:.*?\n)*?"
        r"^```yaml\s*\n(?P<body>.*?)^```",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return ExceptionsSnapshot(parse_error="machine-parseable block not found")
    body = m.group("body")

    entries: dict[str, dict[str, str]] = {}
    all_entries: list[dict[str, str]] = []
    try:
        for entry in _parse_simple_yaml_entries(body):
            cls = entry.get("class_id")
            if not cls:
                continue
            # Preserve every parsed entry (with all fields, incl. optional
            # v2 approver/signoff_commit_sha/signoff_pr_number when present)
            # for the load_agent_review_gate_signoff accessor.
            all_entries.append({str(k): str(v) for k, v in entry.items()})
            if entry.get("runtime_tool_class") in (None, "", "n/a — not a runtime tool-call class"):
                # Not a runtime class — skip the runtime-tool-class index
                # (still preserved in all_entries; the parity test sees it
                # via the raw block instead).
                continue
            rc = entry.get("runtime_tool_class")
            entries[str(rc)] = {
                "class_id": str(cls),
                "scope": str(entry.get("scope", "")),
                "granted_date": str(entry.get("granted_date", "")),
                "review_condition": str(entry.get("review_condition", "")),
            }
    except Exception as e:
        return ExceptionsSnapshot(parse_error=str(e))
    return ExceptionsSnapshot(entries=entries, all_entries=all_entries)


AGENT_REVIEW_GATE_SIGNOFF_CLASS_ID: Final[str] = "agent_auto_merge_code_review"
DEFERRED_CHIP_AUTONOMOUS_IMPLEMENTATION_CLASS_ID: Final[str] = (
    "deferred_chip_autonomous_implementation"
)


def load_agent_review_gate_signoff(
    path: Path | str = EXCEPTIONS_PATH,
    *,
    lane_id: str | None = None,
) -> SignoffBlock | None:
    """Return the ``agent_auto_merge_code_review`` sign-off entry, or None.

    Consumers: the phase-2 PR #5 ``agent_review_gate_pre_merge_check``
    module and the phase-2 PR #3 ``collect_agent_review_gate_freshness``
    daily-health check's kill-switch-vs-active-consumer disambiguation.

    ``lane_id`` selects among multiple ``agent_auto_merge_code_review``
    entries so the shared recovery-lane sign-off (no ``lane_id``) and a
    lane-scoped agent-dispatch sign-off (a per-lane ``lane_id``) can coexist
    as distinct entries carrying distinct ``signoff_commit_sha`` authorizations:
      - ``lane_id is None`` (default; every existing caller) → the FIRST entry
        that itself has NO ``lane_id`` — the recovery-lane / generic block.
        A lane-scoped entry is NEVER returned for a lane-less request, so
        adding a lane row can never re-target the recovery lane's resolution
        (order-independent).
      - ``lane_id`` given → the FIRST entry whose own ``lane_id`` equals it.

    Returns ``None`` in these absent-signal shapes:
      - EXCEPTIONS.md is unreadable / the machine block is absent (any
        ``parse_error`` in the underlying ExceptionsSnapshot).
      - No ``agent_auto_merge_code_review`` entry matches the requested lane
        scope (the phase-2 landing state, or a lane whose row isn't authored
        yet — a signoff_missing, not a crash).

    Fail-open: never raises. The PR #5 verifier distinguishes
    ``signoff_missing`` (this returns None) from ``signoff_unverified``
    (this returns a block but the GitHub-approval provenance check fails).
    """
    snap = load_exceptions(path)
    if snap.parse_error is not None:
        return None
    for entry in snap.all_entries:
        if entry.get("class_id") != AGENT_REVIEW_GATE_SIGNOFF_CLASS_ID:
            continue
        entry_lane = entry.get("lane_id") or None
        if lane_id is None:
            # Recovery/generic request: only a lane-LESS entry qualifies.
            if entry_lane is not None:
                continue
        else:
            # Lane-scoped request: only the matching lane entry qualifies.
            if entry_lane != lane_id:
                continue
        # v2 optional fields — normalize "" to None so a consumer's
        # `if block.approver:` reads as absent, not present-but-empty.
        approver = entry.get("approver") or None
        signoff_commit_sha = entry.get("signoff_commit_sha") or None
        signoff_pr_number = entry.get("signoff_pr_number") or None

        def _str_tuple(value: Any) -> tuple[str, ...]:
            # H1 list fields — coerce a list value to a tuple of str; a bare
            # scalar or missing value normalizes to () (absent envelope), never a
            # char-iterated string.
            #
            # The hand-written machine-block parser (``_parse_simple_yaml_entries``
            # → ``_apply_kv``) stores EVERY entry value as a string, so an inline
            # list authored as ``fence_globs: [src/**, tests/**]`` arrives here as
            # the STRING ``"[src/**, tests/**]"`` (it cannot represent a real
            # nested list — block-list form would be mis-read as new entries).
            # Parse that bracketed-inline-list shape into a tuple so the H1
            # lane-scope envelope (``fence_globs`` / ``permitted_item_classes``)
            # verifies; without this an authored lane-scoped sign-off row loads
            # with an EMPTY envelope and ``verify_lane_scope`` fails-closed
            # ``fence_globs_absent`` (the rung-3 lane could never merge).
            if isinstance(value, (list, tuple)):
                return tuple(str(x) for x in value)
            if isinstance(value, str):
                s = value.strip()
                if s.startswith("[") and s.endswith("]"):
                    inner = s[1:-1].strip()
                    if not inner:
                        return ()
                    return tuple(
                        item.strip().strip("'\"").strip()
                        for item in inner.split(",")
                        if item.strip().strip("'\"").strip()
                    )
            return ()

        return SignoffBlock(
            class_id=entry["class_id"],
            scope=entry.get("scope", ""),
            granted_date=entry.get("granted_date", ""),
            review_condition=entry.get("review_condition", ""),
            runtime_tool_class=entry.get("runtime_tool_class", ""),
            approver=approver,
            signoff_commit_sha=signoff_commit_sha,
            signoff_pr_number=signoff_pr_number,
            # H1/H3/H5 lane-scope envelope (optional; "" → None per existing norm).
            lane_id=entry.get("lane_id") or None,
            target_repo=entry.get("target_repo") or None,
            base_branch=entry.get("base_branch") or None,
            fence_globs=_str_tuple(entry.get("fence_globs")),
            permitted_item_classes=_str_tuple(entry.get("permitted_item_classes")),
            merge_method=entry.get("merge_method") or None,
            actor=entry.get("actor") or None,
            expires_at=entry.get("expires_at") or None,
        )
    return None


def load_deferred_chip_autonomous_implementation(
    path: Path | str = EXCEPTIONS_PATH,
) -> dict[str, str] | None:
    """Return the explicit deferred-chip implementation authorization, if present.

    This reader intentionally does not open the overnight flag or authorize a
    specific change. The execution bridge must still require the existing
    overnight ``enabled`` flag, a contracted Tier 1/2 item, and the agent
    dispatch lane's four-part gate before it can perform any code write.
    """
    snap = load_exceptions(path)
    if snap.parse_error is not None:
        return None
    for entry in snap.all_entries:
        if entry.get("class_id") == DEFERRED_CHIP_AUTONOMOUS_IMPLEMENTATION_CLASS_ID:
            return dict(entry)
    return None


def _parse_simple_yaml_entries(body: str) -> list[dict[str, str]]:
    """Minimal YAML list-of-dicts parser for the EXCEPTIONS machine block.

    Accepts the exact shape:

        version: 1
        entries:
          - class_id: <str>
            scope: <str>
            granted_date: <YYYY-MM-DD>
            review_condition: <str>
            runtime_tool_class: <str>
          - ...

    Values may be single- or double-quoted (quotes stripped). No block
    scalars, no anchors, no nested lists. Any structure outside this shape
    yields a ValueError.
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_entries = False
    version_seen = False
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0:
            in_entries = stripped.startswith("entries:")
            if stripped.startswith("version:"):
                version_seen = True
                val = stripped.split(":", 1)[1].strip()
                # v2 (phase-2 PR #3) is a backward-compatible extension:
                # optional approver/signoff_commit_sha/signoff_pr_number
                # fields on entries that need GitHub-approval provenance.
                if val not in ("1", "2"):
                    raise ValueError(f"unsupported version: {val}")
            if current is not None:
                entries.append(current)
                current = None
            continue

        if not in_entries:
            continue

        if stripped.startswith("- "):
            if current is not None:
                entries.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest:
                _apply_kv(current, rest)
            continue
        if current is not None:
            _apply_kv(current, stripped)
    if current is not None:
        entries.append(current)
    if not version_seen:
        raise ValueError("missing version:")
    return entries


def _apply_kv(entry: dict[str, str], kv: str) -> None:
    if ":" not in kv:
        return
    k, _, v = kv.partition(":")
    v = v.strip()
    if (v.startswith('"') and v.endswith('"')) or (
        v.startswith("'") and v.endswith("'")
    ):
        v = v[1:-1]
    entry[k.strip()] = v


def load_always_on(path: Path | str = ALWAYS_ON_PATH) -> AlwaysOnSnapshot:
    """Parse ALWAYS_ON.md — collect the set of registered plist labels.

    We look for the leftmost column of any markdown table whose header row
    contains a "Label" column, plus any inline mention of a ``com.<x>.<y>``
    label in service tables. Fail-open on read/parse error.
    """
    try:
        text = Path(path).read_text()
    except Exception as e:
        return AlwaysOnSnapshot(parse_error=str(e))

    labels: set[str] = set()
    in_label_table = False
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_label_table:
                if any("label" in c.lower() for c in cells):
                    in_label_table = True
                continue
            if all(re.fullmatch(r"[-:]+", c) for c in cells):
                continue
            if not cells:
                continue
            first = _strip_markdown(cells[0])
            if _looks_like_label(first):
                labels.add(first)
        else:
            if in_label_table and line.strip() == "":
                in_label_table = False

    # Also sweep loose backtick-quoted labels anywhere in the file (covers
    # services listed outside a Label-column table, e.g. `ai.openclaw.gateway`).
    for m in re.finditer(r"`([a-z][\w.-]+\.[\w.-]+)`", text):
        cand = m.group(1)
        if _looks_like_label(cand):
            labels.add(cand)
    return AlwaysOnSnapshot(labels=labels)


def _looks_like_label(s: str) -> bool:
    return bool(re.fullmatch(r"[a-z][\w-]*(\.[\w-]+)+", s))


# --------------------------------------------------------------------------
# Public evaluators — the dispatcher entry points
# --------------------------------------------------------------------------


def evaluate_bash(
    command: str,
    *,
    allowlist: AllowlistSnapshot | None = None,
    always_on: AlwaysOnSnapshot | None = None,
) -> EvalResult:
    """Classify a Bash tool call. Always returns an ``EvalResult`` (never raises).

    The classifier evaluates two orthogonal classes:
      1. Outbound WRITES → ALLOWLIST lookup.
      2. LaunchAgents/Daemons CREATE/START → ALWAYS_ON lookup.
    """
    try:
        return _evaluate_bash_inner(command, allowlist=allowlist, always_on=always_on)
    except Exception as e:
        return EvalResult(outcome="allow_error", error=repr(e), tool="Bash")


def _evaluate_bash_inner(
    command: str,
    *,
    allowlist: AllowlistSnapshot | None,
    always_on: AlwaysOnSnapshot | None,
) -> EvalResult:
    # Class 2: ALWAYS_ON create/destroy (evaluated first — a `launchctl load`
    # is BOTH a launchd op AND does not contain a URL, so this check is cheap
    # and unambiguous).
    launchd = classify_launchd_command(command)
    if launchd is not None:
        kind, label = launchd
        if kind == "destroy":
            # Cleanup path is never blocked.
            return EvalResult(outcome="allow", tool="Bash")
        # kind == "create"
        if label is None:
            return EvalResult(
                outcome="allow_unclassifiable",
                unclassifiable_reason="unresolved plist path (shell var?)",
                tool="Bash",
            )
        # Now check the ALWAYS_ON registry
        ao = always_on if always_on is not None else load_always_on()
        if ao.parse_error:
            return EvalResult(
                outcome="allow_error",
                error=f"load_always_on: {ao.parse_error}",
                tool="Bash",
            )
        if label in ao.labels:
            return EvalResult(outcome="allow", tool="Bash")
        return EvalResult(
            outcome="block",
            registry="always_on",
            host_or_label=label,
            row_template=_render_always_on_template(label),
            bypass_hint=_bypass_hint_bash(),
            tool="Bash",
        )

    # Class 1: outbound WRITES.
    if not is_bash_write(command):
        return EvalResult(outcome="allow", tool="Bash")

    # A normal `gh` write targets configured GitHub, so URLs in its body text
    # are content rather than destinations. Explicit host/repository overrides
    # instead become a real destination and must be allowlisted below.
    gh_mutating, gh_host = _gh_mutating_command_target(command)
    if gh_mutating and gh_host is None:
        return EvalResult(outcome="allow", host_or_label="api.github.com", tool="Bash")

    # Check for a bypass token BEFORE registry lookup — the bypass short-
    # circuits any block that would otherwise fire.
    bypass = extract_bypass_reason(command)

    if is_shell_var_url(command):
        return EvalResult(
            outcome="allow_unclassifiable",
            unclassifiable_reason="shell-var URL",
            tool="Bash",
            bypass_reason=bypass,
        )

    host_ports = [(gh_host, None)] if gh_mutating else extract_url_host_ports(command)
    if not host_ports:
        # No URL literal — nothing to look up. Common for `gh pr create` etc.
        # which uses the local repo's origin. Allow.
        return EvalResult(outcome="allow", tool="Bash", bypass_reason=bypass)

    al = allowlist if allowlist is not None else load_allowlist()
    if al.parse_error:
        return EvalResult(
            outcome="allow_error",
            error=f"load_allowlist: {al.parse_error}",
            tool="Bash",
        )

    for host, port in host_ports:
        if _is_localhost_or_tailnet(host, port):
            continue
        if _host_in_allowlist(host, al):
            continue
        if bypass is not None:
            return EvalResult(
                outcome="allow_bypassed",
                registry="allowlist",
                host_or_label=host,
                bypass_reason=bypass,
                tool="Bash",
            )
        return EvalResult(
            outcome="block",
            registry="allowlist",
            host_or_label=host,
            row_template=_render_allowlist_template(host),
            bypass_hint=_bypass_hint_bash(),
            tool="Bash",
        )

    first_host, first_port = host_ports[0]
    outcome = "allow_localhost" if _is_localhost_or_tailnet(first_host, first_port) else "allow"
    return EvalResult(outcome=outcome, host_or_label=first_host, tool="Bash")


def _host_in_allowlist(host: str, al: AllowlistSnapshot) -> bool:
    # Direct known-hosts hit
    if host in al.host_index:
        return True
    # Suffix-hit against known hosts (e.g. api.slack.com → slack.com present)
    for known in _KNOWN_HOSTS:
        if host == known or host.endswith("." + known):
            svc = _KNOWN_HOSTS[known]
            if svc.lower() in al.services:
                return True
    # Also match against ALLOWLIST-declared service names by substring on host
    # (fallback for services whose hosts aren't in _KNOWN_HOSTS but whose
    # service name appears in the domain).
    for svc in al.services:
        if svc in host:
            return True
    return False


def evaluate_mcp(
    tool_name: str,
    tool_input: dict | None = None,
    *,
    allowlist: AllowlistSnapshot | None = None,
    exceptions: ExceptionsSnapshot | None = None,
) -> EvalResult:
    """Classify an MCP tool call. Always returns an ``EvalResult``."""
    try:
        return _evaluate_mcp_inner(
            tool_name, tool_input, allowlist=allowlist, exceptions=exceptions
        )
    except Exception as e:
        return EvalResult(outcome="allow_error", error=repr(e), tool=tool_name)


def _evaluate_mcp_inner(
    tool_name: str,
    tool_input: dict | None,
    *,
    allowlist: AllowlistSnapshot | None,
    exceptions: ExceptionsSnapshot | None,
) -> EvalResult:
    # Strip the `mcp__<server>__` prefix Claude Code adds around MCP tool
    # names so the classifier can match against the wire-name.
    base = _strip_mcp_prefix(tool_name)

    family = _match_mcp_family(base)
    if family is None:
        # Not enumerated — either a read tool (never gated) or an unknown
        # family. Fail-open allow.
        return EvalResult(outcome="allow", tool=tool_name)

    routing = _MCP_FAMILY_TO_REGISTRY_CLASS.get(family, "allowlist_only")

    if routing == "exceptions_only":
        return _evaluate_exceptions_class(family, tool_name, exceptions)

    if routing == "allowlist_only":
        return _evaluate_allowlist_service(family, tool_name, allowlist)

    # allowlist_and_exceptions — both required
    al_result = _evaluate_allowlist_service(family, tool_name, allowlist)
    if al_result.outcome == "block":
        return al_result
    if al_result.outcome.startswith("allow_"):
        # Loader error / unclassifiable — pass through the fail-open outcome
        return al_result
    return _evaluate_exceptions_class(family, tool_name, exceptions)


def _strip_mcp_prefix(tool_name: str) -> str:
    # Claude Code names MCP tools `mcp__<server-slug>__<tool>`; the server
    # slug for Apple Suite is literally `apple_suite`, but some servers use
    # UUID-shaped slugs (e.g. `mcp__c63a8ac8-…__slack_send_message`). Match
    # against the last `__` segment.
    m = re.match(r"^mcp__[^_]+(?:_[^_]+)*__(?P<body>.+)$", tool_name)
    if m:
        return m.group("body")
    # apple_suite tools use `mcp__apple_suite__notes_create_note` shape.
    if tool_name.startswith("mcp__apple_suite__"):
        return tool_name[len("mcp__apple_suite__"):]
    return tool_name


def _match_mcp_family(name: str) -> str | None:
    for family, pat in MCP_MUTATING_TOOLS.items():
        # apple_suite tool names include the leading `apple_suite__` in the
        # wire form; strip it before matching against the per-family regex.
        candidate = name
        if family.startswith("apple_suite_") and not candidate.startswith(
            "apple_suite__"
        ):
            candidate = "apple_suite__" + candidate
        if pat.match(candidate) or pat.match(name):
            return family
    return None


def _evaluate_exceptions_class(
    family: str,
    tool_name: str,
    exceptions: ExceptionsSnapshot | None,
) -> EvalResult:
    class_id = _MCP_FAMILY_TO_EXCEPTION_CLASS.get(family)
    if class_id is None:
        return EvalResult(outcome="allow", tool=tool_name)
    ex = exceptions if exceptions is not None else load_exceptions()
    if ex.parse_error:
        return EvalResult(
            outcome="allow_error",
            error=f"load_exceptions: {ex.parse_error}",
            tool=tool_name,
        )
    if class_id in ex.entries:
        return EvalResult(outcome="allow", tool=tool_name)
    return EvalResult(
        outcome="block",
        registry="exceptions",
        host_or_label=class_id,
        row_template=_render_exceptions_template(class_id),
        bypass_hint=_bypass_hint_mcp(class_id),
        tool=tool_name,
    )


def _evaluate_allowlist_service(
    family: str,
    tool_name: str,
    allowlist: AllowlistSnapshot | None,
) -> EvalResult:
    service_name = _MCP_FAMILY_TO_SERVICE_NAME.get(family)
    if service_name is None:
        return EvalResult(outcome="allow", tool=tool_name)
    al = allowlist if allowlist is not None else load_allowlist()
    if al.parse_error:
        return EvalResult(
            outcome="allow_error",
            error=f"load_allowlist: {al.parse_error}",
            tool=tool_name,
        )
    if service_name in al.services:
        return EvalResult(outcome="allow", tool=tool_name)
    return EvalResult(
        outcome="block",
        registry="allowlist",
        host_or_label=service_name,
        row_template=_render_allowlist_service_template(service_name),
        bypass_hint=_bypass_hint_mcp(service_name),
        tool=tool_name,
    )


# --------------------------------------------------------------------------
# Message rendering
# --------------------------------------------------------------------------


def _render_allowlist_template(host: str) -> str:
    return (
        f"| **{host}** (<fill>) | <auth path> | <scope> | <why authorized> |\n"
        f"  Paste this row into the \"Allowed outbound integrations\" table in\n"
        f"  {ALLOWLIST_PATH}."
    )


def _render_allowlist_service_template(service: str) -> str:
    return (
        f"| **{service.title()}** | <auth path (OAuth / subscription only)> | "
        f"<scope> | <why authorized> |\n"
        f"  Paste this row into the \"Allowed outbound integrations\" table in\n"
        f"  {ALLOWLIST_PATH}."
    )


def _render_exceptions_template(class_id: str) -> str:
    return (
        f"  - class_id: {class_id}\n"
        f"    scope: <what exactly is allowed (be narrow)>\n"
        f"    granted_date: <YYYY-MM-DD>\n"
        f"    review_condition: <what would revoke this>\n"
        f"    runtime_tool_class: {class_id}\n"
        f"  Paste this entry into the \"## Machine-parseable exceptions\" YAML\n"
        f"  block in {EXCEPTIONS_PATH} AND author a matching human-prose section\n"
        f"  above it."
    )


def _render_always_on_template(label: str) -> str:
    return (
        f"| `{label}` | <what it does> | <schedule> | <support level> | $0 |\n"
        f"  Paste this row into the appropriate table in\n"
        f"  {ALWAYS_ON_PATH} — verify the M2 tripwire (does this create a new\n"
        f"  support obligation / SaaS dep / privileged surface?) before adding."
    )


def _bypass_hint_bash() -> str:
    return (
        "  One-off bypass (research / short-lived debug):\n"
        "    <your command> # registry-ok: <one-line reason>\n"
        "  The reason is captured in the runtime audit trail; standing tokens\n"
        "  in tracked code are a code-smell (Phase 2 sweep will flag them)."
    )


def _bypass_hint_mcp(class_or_service: str) -> str:
    return (
        "  MCP calls have no in-command bypass channel — add the registry row\n"
        "  above (or route the work through /plan-init → /plan-review). If the\n"
        "  write is genuinely a one-off, do it through an already-approved\n"
        "  surface (e.g. draft it into Notion via an allowed integration) or\n"
        "  ask the operator to authorize the new class."
    )


def render_block_message(result: EvalResult) -> str:
    """Human-facing block message rendered from an EvalResult with outcome=block.

    Per the plan's Frozen decision "Block-message template splits by tool class":
    - Bash blocks emit 4 segments incl. "Or bypass this one call" (the bypass
      token works per Bash line).
    - MCP blocks emit 3 segments plus a "Note:" segment (no inline bypass; add
      the registry row instead).
    """
    if result.outcome != "block":
        raise ValueError(f"render_block_message called on outcome={result.outcome}")
    reg = result.registry or "unknown"
    reg_path = {
        "allowlist": str(ALLOWLIST_PATH),
        "exceptions": str(EXCEPTIONS_PATH),
        "always_on": str(ALWAYS_ON_PATH),
    }.get(reg, f"<{reg}>")
    one_liner = _block_one_liner(result)
    is_mcp = bool(result.tool and result.tool != "Bash")
    parts = [
        f"BLOCKED: {one_liner}.",
        f"Registry: {reg_path}.",
        "Add this row:",
        result.row_template or "  <template unavailable — investigate>",
    ]
    if is_mcp:
        parts.append("Note:")
    else:
        parts.append("Or bypass this one call (research / one-off) with:")
    parts.append(result.bypass_hint or "  <bypass unavailable>")
    return "\n".join(parts)


def _block_one_liner(result: EvalResult) -> str:
    reg = result.registry
    if reg == "allowlist":
        return (
            f"outbound write to {result.host_or_label} not in ALLOWLIST"
        )
    if reg == "exceptions":
        return (
            f"MCP tool class {result.host_or_label} has no matching EXCEPTIONS "
            f"entry (default-deny)"
        )
    if reg == "always_on":
        return (
            f"new launchd label {result.host_or_label} not in ALWAYS_ON "
            f"(persistent-service tripwire)"
        )
    return "registry violation"


def render_stderr_line(result: EvalResult) -> str | None:
    """Return the canonical stderr audit line for a non-``allow`` outcome.

    ``allow`` and ``allow_localhost`` return None (silent — the common paths).
    Every other outcome produces a single-line ``key=value`` record that
    dispatchers write verbatim to their stderr.
    """
    if result.outcome in ("allow", "allow_localhost"):
        return None
    fields: list[str] = [f"outcome={result.outcome}"]
    if result.registry:
        fields.append(f"class={result.registry}")
    if result.host_or_label:
        # host or class_id — safe to embed verbatim (no shell interpretation)
        fields.append(f"target={result.host_or_label}")
    if result.bypass_reason:
        fields.append(f'reason="{_escape_quotes(result.bypass_reason)}"')
    if result.unclassifiable_reason:
        fields.append(
            f'reason="{_escape_quotes(result.unclassifiable_reason)}"'
        )
    if result.error:
        fields.append(f'error="{_escape_quotes(result.error)}"')
    if result.tool:
        fields.append(f"tool={result.tool}")
    return "registry_enforcement: " + " ".join(fields)


def _escape_quotes(s: str) -> str:
    return s.replace('"', '\\"').replace("\n", " ")


# --------------------------------------------------------------------------
# CLI (developer use only — the dispatchers import the functions directly)
# --------------------------------------------------------------------------


def _cli() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bash", help="Classify a Bash command literal")
    ap.add_argument("--mcp", help="Classify an MCP tool name")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    if args.bash:
        result = evaluate_bash(args.bash)
    elif args.mcp:
        result = evaluate_mcp(args.mcp)
    else:
        ap.print_help()
        return 2

    if args.json:
        print(json.dumps({k: v for k, v in result.__dict__.items() if v is not None}))
    else:
        line = render_stderr_line(result)
        if line:
            print(line)
        else:
            print(f"outcome={result.outcome}")
        if result.outcome == "block":
            print("---")
            print(render_block_message(result))
    return 0 if result.outcome != "block" else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
