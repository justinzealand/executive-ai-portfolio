# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Unit tests for registry_enforcement.

Fixture-driven — every classifier decision, every fail-open path, every
rendering-message shape is exercised over hand-authored snapshots. Never
touches the live registry files (they're the runtime source of truth; tests
must be reproducible across sessions).
"""
from __future__ import annotations

import re as _re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import registry_enforcement as R


# ------------------------------------------------------------------
# Snapshot fixtures — hand-built so tests never depend on the live files
# ------------------------------------------------------------------


@pytest.fixture()
def allowlist_full() -> R.AllowlistSnapshot:
    """Snapshot with every Phase 1 service present."""
    return R.AllowlistSnapshot(
        services={
            svc: {"raw_name": svc.title(), "auth": "OAuth", "scope": "", "reason": ""}
            for svc in (
                "slack", "gmail", "google calendar", "google drive",
                "github", "notion", "todoist", "zoom", "bear",
                "openai", "anthropic", "telegram", "granola", "spotify",
                "tailscale tailnet", "discord",
            )
        },
        host_index={
            host: R._KNOWN_HOSTS[host]
            for host in R._KNOWN_HOSTS
            if R._KNOWN_HOSTS[host] in {
                "slack", "gmail", "google calendar", "google drive", "github",
                "notion", "todoist", "zoom", "openai", "anthropic", "telegram",
                "granola", "spotify",
            }
        },
    )


@pytest.fixture()
def allowlist_minimal() -> R.AllowlistSnapshot:
    """Snapshot with only Slack present — for negative-match tests."""
    return R.AllowlistSnapshot(
        services={"slack": {"raw_name": "Slack", "auth": "", "scope": "", "reason": ""}},
        host_index={"slack.com": "slack", "hooks.slack.com": "slack"},
    )


@pytest.fixture()
def exceptions_gcal_only() -> R.ExceptionsSnapshot:
    """Snapshot with `google_calendar_write` granted, iOS classes absent."""
    return R.ExceptionsSnapshot(
        entries={
            "google_calendar_write": {
                "class_id": "google_calendar_write",
                "scope": "interview events",
                "granted_date": "pre-2026-06-30",
                "review_condition": "scope broadens",
            }
        }
    )


@pytest.fixture()
def exceptions_empty() -> R.ExceptionsSnapshot:
    return R.ExceptionsSnapshot(entries={})


@pytest.fixture()
def always_on_typical() -> R.AlwaysOnSnapshot:
    return R.AlwaysOnSnapshot(
        labels={
            "com.justin.daily-health-check",
            "com.justin.daily-code-audit",
            "com.justin.cross-agent-review-smoke",
            "ai.openclaw.gateway",
            "com.openclaw.dashboard",
        }
    )


# ==================================================================
# is_bash_write — positive cases (predicate returns True)
# ==================================================================


@pytest.mark.parametrize(
    "cmd",
    [
        "curl -X POST https://example.com -d body",
        "curl -X PUT https://x.com -d @body",
        "curl -X PATCH https://x.com",
        "curl -X DELETE https://x.com",
        "curl --request POST https://x.com",
        'curl -d "foo=bar" https://x.com',
        "curl --data '{}' https://x.com",
        "curl --data-raw '{}' https://x.com",
        "curl --data-binary @body https://x.com",
        "curl --data-urlencode field=value https://x.com",
        "curl -F file=@x.pdf https://x.com",
        "curl --form file=@x https://x.com",
        "curl -T body https://x.com/put",
        "curl --upload-file body https://x.com/put",
        "wget --method=POST https://x.com",
        "gh api -X POST /repos/foo/bar/issues",
        "gh api /repos/foo/bar -f name=x",
        "gh api /repos/foo/bar -F name=x",
        "gh api /repos/foo/bar --field name=x",
        "gh api /repos/foo/bar --raw-field name=x",
        "gh pr create --title 'x' --body 'y'",
        "gh pr edit 42 --title 'new'",
        "gh pr comment 42 --body 'hi'",
        "gh pr close 42",
        "gh pr reopen 42",
        "gh pr merge 42 --squash",
        "gh pr review 42 --approve",
        "gh pr ready 42",
        "gh issue create --title 'x'",
        "gh issue close 3",
        "gh issue comment 3 --body 'x'",
        "gh release create v1",
        "gh release edit v1",
        "gh release upload v1 file.tgz",
        "gh workflow run foo",
        "gh workflow enable foo",
        "gh run rerun 12345",
        "gh run cancel 12345",
        "gh run delete 12345",
        "http POST https://x.com field=value",
        "http PUT https://x.com",
    ],
)
def test_is_bash_write_positive(cmd: str) -> None:
    assert R.is_bash_write(cmd), f"expected write, got read: {cmd!r}"


# ==================================================================
# is_bash_write — negative cases (predicate returns False) — the pass-1 P0
# defense and the pass-2 P1 fix (gh diff/checkout/watch).
# ==================================================================


@pytest.mark.parametrize(
    "cmd",
    [
        # bare curl / wget GETs
        "curl https://example.com/thing.html",
        "curl -o out.txt https://x.com",
        "curl -L https://x.com",
        "curl -s https://x.com",
        "curl -i -L https://x.com",
        "wget https://example.com/",
        "wget -O out.html https://x.com",
        # gh api bare GET
        "gh api /repos/foo/bar",
        "gh api /repos/foo/bar/issues",
        # gh pr read/local — pass-2 P1 fix
        "gh pr view 42",
        "gh pr list",
        "gh pr status",
        "gh pr checks",
        "gh pr diff 42",  # pass-2 P1
        "gh pr checkout 42",  # pass-2 P1
        # gh issue read
        "gh issue view 3",
        "gh issue list",
        "gh issue status",
        # gh release read
        "gh release view v1",
        "gh release download v1",
        "gh release list",
        # gh workflow / run read
        "gh workflow view foo",
        "gh workflow list",
        "gh run view 999",
        "gh run list",
        "gh run watch 999",  # pass-2 P1
        # everyday commands
        "ls -la",
        "cat foo.md",
        "grep -r 'x' .",
        "git status",
        "git log --oneline -5",
    ],
)
def test_is_bash_write_negative(cmd: str) -> None:
    assert not R.is_bash_write(cmd), f"expected read, got write: {cmd!r}"


# ==================================================================
# ALLOWLIST — write hits (block on unknown), non-hits (allow bare reads),
# localhost/tailnet, shell-var indirection, bypass token, parse-error fail-open.
# ==================================================================


def test_allowlist_hit_slack_allowed(allowlist_full: R.AllowlistSnapshot) -> None:
    r = R.evaluate_bash(
        "curl -X POST https://slack.com/api/chat.postMessage -d text=hi",
        allowlist=allowlist_full,
    )
    assert r.outcome == "allow"


def test_allowlist_hit_gh_api_allowed(allowlist_full: R.AllowlistSnapshot) -> None:
    r = R.evaluate_bash(
        "gh api -X POST /repos/operator-handle/foo/issues -f title=x",
        allowlist=allowlist_full,
    )
    # `gh` mutating calls only ever target the configured GitHub host
    # (api.github.com, already ALLOWLIST-covered). The classifier short-
    # circuits before URL extraction runs, so the outcome is `allow` keyed
    # on api.github.com — regardless of whether a URL literal appears
    # elsewhere in the command.
    assert r.outcome == "allow"
    assert r.host_or_label == "api.github.com"


# Regression pinning for the 2026-07-01 `gh` body-URL false positive
# (task_40677135). Before the short-circuit, `gh pr create --body '…https://
# claude.com/…'` picked up `claude.com` as the target host and blocked, so
# the live-fire shipping PR needed a `# registry-ok:` bypass. Both shapes
# below must classify as allow-api.github.com WITHOUT any bypass token.

_GH_WRITE_WITH_BODY_URLS = [
    # The exact heredoc shape that failed on projects#805 + claude-code-hooks#41.
    (
        "gh pr create --title x --body \"$(cat <<'EOF'\n"
        "## Summary\n"
        "one-line summary\n\n"
        "Generated with [Claude Code](https://claude.com/claude-code)\n"
        "EOF\n"
        ")\""
    ),
    # Non-heredoc form: URL inline in --body.
    "gh pr edit 42 --body 'see https://example.com/doc for context'",
    # URL in --field (gh api form).
    "gh api -X POST /repos/foo/bar/issues -f 'body=see https://example.com/doc'",
    # URL after a mutating verb inside `--title`.
    "gh issue create --title 'ref https://example.com/x' --body 'y'",
    # Release-upload with a URL in --notes.
    "gh release create v1 --notes 'ships https://claude.com/claude-code fix'",
]


@pytest.mark.parametrize("cmd", _GH_WRITE_WITH_BODY_URLS)
def test_gh_write_with_body_url_never_blocks(
    cmd: str, allowlist_full: R.AllowlistSnapshot
) -> None:
    r = R.evaluate_bash(cmd, allowlist=allowlist_full)
    assert r.outcome == "allow", f"gh body-URL should never block: {cmd!r}"
    assert r.host_or_label == "api.github.com"
    # No bypass token was needed to reach `allow`.
    assert r.bypass_reason is None


def test_gh_write_short_circuit_does_not_leak_to_non_gh(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    """Guard against over-fix: only `gh` gets the short-circuit."""
    # `curl -X POST` to an unknown host must STILL block, even with the same
    # body-URL shape that the gh short-circuit ignores.
    r = R.evaluate_bash(
        "curl -X POST https://nothing.example.com/thing -d 'body https://claude.com/'",
        allowlist=allowlist_full,
    )
    assert r.outcome == "block"
    assert r.host_or_label == "nothing.example.com"


@pytest.mark.parametrize(
    "cmd",
    [
        "GH_HOST=untrusted.example gh issue create --title x",
        "gh api --hostname untrusted.example -X POST /repos/a/b/issues -f title=x",
        "gh -R untrusted.example/a/b issue create --title x",
    ],
)
def test_gh_write_explicit_host_override_requires_allowlist(
    cmd: str, allowlist_full: R.AllowlistSnapshot
) -> None:
    r = R.evaluate_bash(cmd, allowlist=allowlist_full)
    assert r.outcome == "block"
    assert r.host_or_label == "untrusted.example"


def test_every_write_url_must_be_allowlisted(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_bash(
        "curl -X POST https://slack.com/api --next "
        "https://untrusted.example/collect -d text=hello",
        allowlist=allowlist_full,
    )
    assert r.outcome == "block"
    assert r.host_or_label == "untrusted.example"


def test_gh_read_still_never_writes(allowlist_full: R.AllowlistSnapshot) -> None:
    """`gh pr view/list/diff/checkout/watch` are reads — `is_bash_write` is False.

    The short-circuit fires only after `is_bash_write` returns True, so a gh
    READ command reaches the early `allow` return via the read path, not via
    the short-circuit. This test pins the assumption; if `_has_gh_write_shape`
    ever over-classifies a read subcommand as a write, THIS test still passes
    (short-circuit produces allow either way), but the sibling
    `test_is_bash_write_negative` covers the class distinction directly.
    """
    for cmd in [
        "gh pr view 42",
        "gh pr diff 42",
        "gh pr list",
        "gh issue view 3",
    ]:
        r = R.evaluate_bash(cmd, allowlist=allowlist_full)
        assert r.outcome == "allow", cmd


def test_allowlist_block_unknown_host(allowlist_full: R.AllowlistSnapshot) -> None:
    r = R.evaluate_bash(
        "curl -X POST https://not-in-allowlist.example.com -d body",
        allowlist=allowlist_full,
    )
    assert r.outcome == "block"
    assert r.registry == "allowlist"
    assert r.host_or_label == "not-in-allowlist.example.com"
    assert r.row_template is not None
    assert r.bypass_hint is not None


def test_allowlist_bare_get_never_blocks(allowlist_full: R.AllowlistSnapshot) -> None:
    """Charter S6 C5 one-off-read exclusion — pass-1 P0 regression."""
    for cmd in [
        "curl https://random-domain.example",
        "curl -L https://random-domain.example",
        "wget https://random-domain.example/file",
        "gh api /repos/random/random",
    ]:
        r = R.evaluate_bash(cmd, allowlist=allowlist_full)
        assert r.outcome == "allow", f"bare read should never block: {cmd!r}"


def test_allowlist_localhost_never_classified(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    for cmd in [
        "curl -X POST http://127.0.0.1:8378/foo -d body",
        "curl -X POST http://localhost:18789/x -d body",
        "curl -X POST http://0.0.0.0:8080/x -d body",
    ]:
        r = R.evaluate_bash(cmd, allowlist=allowlist_full)
        assert r.outcome == "allow_localhost"


def test_tailnet_port_exempted_when_not_8443(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    """Pass-2 P0-2 fix: tailnet-internal port bypasses ALLOWLIST."""
    r = R.evaluate_bash(
        "curl -X POST https://redacted-host.ts.net:18789/foo -d body",
        allowlist=allowlist_full,
    )
    assert r.outcome == "allow_localhost"


def test_tailnet_port_8443_goes_through_allowlist(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    """Pass-2 P0-2 fix: :8443 is the public Funnel; must NOT be blanket-exempted."""
    r = R.evaluate_bash(
        "curl -X POST https://redacted-host.ts.net:8443/approval -d body",
        allowlist=allowlist_full,
    )
    # tailscale tailnet IS in the ALLOWLIST fixture as "tailscale tailnet",
    # but the host `*.ts.net` isn't in `_KNOWN_HOSTS`. The fallback host-in-
    # service-name check (`"tailscale tailnet" in "redacted-host.ts.net"`)
    # is False, so this classifies as unknown-host → block. That is the
    # intended behavior: the operator opts a specific tailnet endpoint into
    # ALLOWLIST via a real row + `_KNOWN_HOSTS` mapping, not via blanket
    # hostname suffix.
    assert r.outcome == "block"
    assert r.registry == "allowlist"


def test_shell_var_url_fails_open(allowlist_full: R.AllowlistSnapshot) -> None:
    r = R.evaluate_bash(
        'curl -X POST "$URL" -d @body',
        allowlist=allowlist_full,
    )
    assert r.outcome == "allow_unclassifiable"
    assert r.unclassifiable_reason


def test_bypass_token_allowed(allowlist_full: R.AllowlistSnapshot) -> None:
    r = R.evaluate_bash(
        "curl -X POST https://not-in-allowlist.example.com -d x # registry-ok: one-time research",
        allowlist=allowlist_full,
    )
    assert r.outcome == "allow_bypassed"
    assert r.bypass_reason == "one-time research"


def test_allowlist_parse_error_yields_allow_error() -> None:
    bad = R.AllowlistSnapshot(parse_error="synthetic")
    r = R.evaluate_bash(
        "curl -X POST https://x.example.com -d body",
        allowlist=bad,
        always_on=R.AlwaysOnSnapshot(),
    )
    assert r.outcome == "allow_error"
    assert "load_allowlist" in (r.error or "")


# ==================================================================
# MCP — routing model (iOS → EXCEPTIONS only; gcal → both; else → ALLOWLIST).
# Read-side non-hits verified per family.
# ==================================================================


@pytest.mark.parametrize(
    "tool_name",
    [
        "mcp__apple_suite__notes_create_note",
        "apple_suite__notes_create_folder",
        "mcp__apple_suite__notes_update_note",
        "mcp__apple_suite__notes_delete_note",
    ],
)
def test_ios_notes_write_blocks_when_absent(
    tool_name: str,
    exceptions_empty: R.ExceptionsSnapshot,
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_mcp(tool_name, allowlist=allowlist_full, exceptions=exceptions_empty)
    assert r.outcome == "block"
    assert r.registry == "exceptions"
    assert r.host_or_label == "ios_notes_write"


def test_ios_reminders_write_blocks_when_absent(
    exceptions_empty: R.ExceptionsSnapshot,
) -> None:
    r = R.evaluate_mcp(
        "mcp__apple_suite__reminders_create_reminder",
        exceptions=exceptions_empty,
    )
    assert r.outcome == "block"
    assert r.host_or_label == "ios_reminders_write"


def test_ios_calendar_write_blocks_when_absent(
    exceptions_empty: R.ExceptionsSnapshot,
) -> None:
    r = R.evaluate_mcp(
        "mcp__apple_suite__calendar_create_event",
        exceptions=exceptions_empty,
    )
    assert r.outcome == "block"
    assert r.host_or_label == "ios_calendar_write"


@pytest.mark.parametrize(
    "tool_name",
    [
        # per-family reads should NEVER be classified — pass-1 P1 regression.
        "mcp__apple_suite__notes_list_notes",
        "mcp__apple_suite__notes_get_note",
        "mcp__apple_suite__notes_search_notes",
        "mcp__apple_suite__reminders_list_reminders",
        "mcp__apple_suite__reminders_search_reminders",
        "mcp__apple_suite__calendar_list_events",
        "mcp__apple_suite__calendar_search_events",
        "mcp__x__slack_read_channel",
        "mcp__x__slack_search_users",
        "mcp__x__slack_read_thread",
        "mcp__x__gmail_find_email",
        "mcp__x__gmail_get_attachment_by_filename",
        "mcp__x__google_calendar_find_events",
        "mcp__x__google_calendar_get_calendar_information",
        "mcp__x__google_docs_get_document_content",
        "mcp__x__google_docs_find_a_document",
        "mcp__x__google_drive_find_a_file",
        "mcp__x__google_drive_download_file_content",
        "mcp__x__google_sheets_get_spreadsheet_by_id",
        "mcp__x__notion_query_data_source_advanced",
        "mcp__x__notion_get_page_and_children",
        "mcp__x__notion_retrieve_database",
        "mcp__x__zoom_find_meeting_webinar",
        "mcp__x__zoom_get_meeting_summary",
        "mcp__x__todoist_list_tasks",
        "mcp__x__todoist_view_task",
        "mcp__x__bear_get_note",
        "mcp__x__bear_search_notes",
    ],
)
def test_read_side_never_classified(
    tool_name: str,
    exceptions_empty: R.ExceptionsSnapshot,
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_mcp(tool_name, allowlist=allowlist_full, exceptions=exceptions_empty)
    assert r.outcome == "allow", f"read tool should never classify: {tool_name!r}"


def test_google_calendar_write_needs_both_registries(
    allowlist_full: R.AllowlistSnapshot,
    exceptions_gcal_only: R.ExceptionsSnapshot,
) -> None:
    r = R.evaluate_mcp(
        "mcp__x__google_calendar_create_detailed_event",
        allowlist=allowlist_full,
        exceptions=exceptions_gcal_only,
    )
    assert r.outcome == "allow"


def test_google_calendar_write_blocks_when_exception_missing(
    allowlist_full: R.AllowlistSnapshot,
    exceptions_empty: R.ExceptionsSnapshot,
) -> None:
    r = R.evaluate_mcp(
        "mcp__x__google_calendar_create_detailed_event",
        allowlist=allowlist_full,
        exceptions=exceptions_empty,
    )
    assert r.outcome == "block"
    assert r.registry == "exceptions"


def test_google_calendar_write_blocks_when_allowlist_missing(
    allowlist_minimal: R.AllowlistSnapshot,
    exceptions_gcal_only: R.ExceptionsSnapshot,
) -> None:
    """Google not in allowlist → block at allowlist stage."""
    r = R.evaluate_mcp(
        "mcp__x__google_calendar_create_detailed_event",
        allowlist=allowlist_minimal,
        exceptions=exceptions_gcal_only,
    )
    assert r.outcome == "block"
    assert r.registry == "allowlist"


def test_allowlist_only_mcp_slack_allowed(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_mcp("mcp__x__slack_send_message", allowlist=allowlist_full)
    assert r.outcome == "allow"


def test_allowlist_only_mcp_notion_allowed(
    allowlist_full: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_mcp("mcp__x__notion_create_page", allowlist=allowlist_full)
    assert r.outcome == "allow"


def test_allowlist_only_mcp_blocks_when_service_missing(
    allowlist_minimal: R.AllowlistSnapshot,
) -> None:
    r = R.evaluate_mcp("mcp__x__notion_create_page", allowlist=allowlist_minimal)
    assert r.outcome == "block"
    assert r.registry == "allowlist"


def test_unenumerated_mcp_family_fails_open() -> None:
    r = R.evaluate_mcp("mcp__x__foobar_send_message")
    assert r.outcome == "allow"


# ==================================================================
# ALWAYS_ON — CREATE/START blocks unregistered; STOP/REMOVE never blocks.
# ==================================================================


@pytest.mark.parametrize(
    "cmd",
    [
        "cp /tmp/x.plist ~/Library/LaunchAgents/com.unreg.foo.plist",
        "mv /tmp/x.plist ~/Library/LaunchAgents/com.unreg.foo.plist",
        "install -m 644 /tmp/x.plist ~/Library/LaunchAgents/com.unreg.foo.plist",
        "cat > ~/Library/LaunchAgents/com.unreg.foo.plist <<EOF\ndata\nEOF",
        "launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.unreg.foo.plist",
        "launchctl load ~/Library/LaunchAgents/com.unreg.foo.plist",
    ],
)
def test_always_on_create_blocks_unregistered(
    cmd: str,
    always_on_typical: R.AlwaysOnSnapshot,
) -> None:
    r = R.evaluate_bash(cmd, always_on=always_on_typical)
    assert r.outcome == "block", f"create-shape should block unregistered: {cmd!r}"
    assert r.registry == "always_on"
    assert r.host_or_label == "com.unreg.foo"


@pytest.mark.parametrize(
    "cmd",
    [
        "cp /tmp/x.plist ~/Library/LaunchAgents/com.justin.daily-health-check.plist",
        "launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.justin.daily-health-check.plist",
        "launchctl load ~/Library/LaunchAgents/com.justin.daily-health-check.plist",
    ],
)
def test_always_on_create_allowed_registered(
    cmd: str,
    always_on_typical: R.AlwaysOnSnapshot,
) -> None:
    r = R.evaluate_bash(cmd, always_on=always_on_typical)
    assert r.outcome == "allow", f"registered label should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "rm ~/Library/LaunchAgents/com.unreg.foo.plist",
        "launchctl bootout gui/501/com.unreg.foo",
        "launchctl unload ~/Library/LaunchAgents/com.unreg.foo.plist",
        "launchctl remove com.unreg.foo",
        "launchctl disable gui/501/com.unreg.foo",
        # kickstart -k on registered label is a restart — never blocked.
        "launchctl kickstart -k gui/501/com.justin.daily-health-check",
    ],
)
def test_always_on_destroy_never_blocks(
    cmd: str,
    always_on_typical: R.AlwaysOnSnapshot,
) -> None:
    r = R.evaluate_bash(cmd, always_on=always_on_typical)
    assert r.outcome == "allow", f"destroy shape should never block: {cmd!r}"


def test_always_on_bare_read_never_classified(
    always_on_typical: R.AlwaysOnSnapshot,
) -> None:
    for cmd in [
        "cat ~/Library/LaunchAgents/com.unreg.foo.plist",
        "mv ~/foo.plist ~/tmp/",  # target NOT under LaunchAgents
    ]:
        r = R.evaluate_bash(cmd, always_on=always_on_typical)
        assert r.outcome == "allow", cmd


# ==================================================================
# Fail-open regression: exception inside classifier → allow_error, not block.
# ==================================================================


def test_evaluate_bash_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        R, "load_allowlist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    r = R.evaluate_bash("curl -X POST https://x.example -d body")
    assert r.outcome == "allow_error"
    assert "boom" in (r.error or "")


def test_evaluate_mcp_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        R,
        "load_exceptions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("oops")),
    )
    r = R.evaluate_mcp("mcp__apple_suite__notes_create_note")
    assert r.outcome == "allow_error"
    assert "oops" in (r.error or "")


# ==================================================================
# render_stderr_line — per-outcome shape.
# ==================================================================


def test_stderr_line_silent_on_allow() -> None:
    assert R.render_stderr_line(R.EvalResult(outcome="allow")) is None


def test_stderr_line_silent_on_localhost() -> None:
    r = R.EvalResult(outcome="allow_localhost", host_or_label="127.0.0.1")
    assert R.render_stderr_line(r) is None


def test_stderr_line_bypass_shape() -> None:
    r = R.EvalResult(
        outcome="allow_bypassed",
        registry="allowlist",
        host_or_label="example.com",
        bypass_reason="one-time research",
        tool="Bash",
    )
    line = R.render_stderr_line(r)
    assert line is not None
    assert "outcome=allow_bypassed" in line
    assert "class=allowlist" in line
    assert "target=example.com" in line
    assert 'reason="one-time research"' in line
    assert "tool=Bash" in line


def test_stderr_line_unclassifiable_shape() -> None:
    r = R.EvalResult(
        outcome="allow_unclassifiable",
        registry="allowlist",
        unclassifiable_reason="shell-var URL",
        tool="Bash",
    )
    line = R.render_stderr_line(r)
    assert 'reason="shell-var URL"' in (line or "")


def test_stderr_line_block_shape() -> None:
    r = R.EvalResult(
        outcome="block",
        registry="exceptions",
        host_or_label="ios_notes_write",
        tool="mcp__apple_suite__notes_create_note",
    )
    line = R.render_stderr_line(r)
    assert line is not None
    assert "outcome=block" in line
    assert "class=exceptions" in line
    assert "target=ios_notes_write" in line


def test_stderr_line_error_shape() -> None:
    r = R.EvalResult(
        outcome="allow_error", error='RuntimeError("boom")', tool="Bash"
    )
    line = R.render_stderr_line(r)
    assert "outcome=allow_error" in (line or "")
    assert 'error="RuntimeError(\\"boom\\")"' in (line or "")


# ==================================================================
# render_block_message — Bash vs MCP split.
# ==================================================================


def test_block_message_bash_has_bypass_segment() -> None:
    r = R.EvalResult(
        outcome="block",
        registry="allowlist",
        host_or_label="example.com",
        row_template="<template>",
        bypass_hint="  bypass: # registry-ok:",
        tool="Bash",
    )
    msg = R.render_block_message(r)
    assert "BLOCKED:" in msg
    assert "Registry:" in msg
    assert "Add this row" in msg
    assert "Or bypass this one call" in msg
    assert "# registry-ok:" in msg


def test_block_message_mcp_has_no_inline_bypass_segment() -> None:
    r = R.EvalResult(
        outcome="block",
        registry="exceptions",
        host_or_label="ios_notes_write",
        row_template="<yaml entry>",
        bypass_hint=(
            "  MCP calls have no in-command bypass channel — add the registry row\n"
            "  above ..."
        ),
        tool="mcp__apple_suite__notes_create_note",
    )
    msg = R.render_block_message(r)
    assert "BLOCKED:" in msg
    assert "Registry:" in msg
    assert "Add this row" in msg
    # The MCP hint text explicitly says "no in-command bypass channel"
    assert "no in-command bypass channel" in msg


def test_block_message_refuses_non_block_outcome() -> None:
    with pytest.raises(ValueError):
        R.render_block_message(R.EvalResult(outcome="allow"))


# ==================================================================
# _KNOWN_HOSTS binding — every service referenced by an MCP family must be
# discoverable via at least one known-host entry OR named in ALLOWLIST-only
# routing without a URL host (both are valid — the classifier maps by MCP
# tool-name for MCP calls; the host mapping is for Bash outbound writes).
# ==================================================================


def test_known_hosts_service_names_are_valid() -> None:
    """Every _KNOWN_HOSTS entry maps to a service short-name recognized by
    _normalize_service_name (proves the two mappings stay in sync)."""
    for host, svc in R._KNOWN_HOSTS.items():
        assert R._normalize_service_name(svc) == svc, (
            f"_KNOWN_HOSTS host {host!r} maps to {svc!r} which does not "
            f"round-trip through _normalize_service_name"
        )


def test_known_hosts_covers_all_runtime_write_services() -> None:
    """Every ALLOWLIST-only-routed MCP family that goes over the outbound
    network has a corresponding known-host entry. Local-only MCP families
    (Bear) are excluded — the outbound-Bash path can never reach them."""
    runtime_write_services = set()
    for family in R.MCP_MUTATING_TOOLS:
        if family in R._MCP_LOCAL_ONLY_FAMILIES:
            continue
        routing = R._MCP_FAMILY_TO_REGISTRY_CLASS.get(family, "allowlist_only")
        if routing == "exceptions_only":
            continue
        svc = R._MCP_FAMILY_TO_SERVICE_NAME.get(family)
        if svc:
            runtime_write_services.add(svc)
    known_services = set(R._KNOWN_HOSTS.values())
    missing = runtime_write_services - known_services
    assert not missing, f"_KNOWN_HOSTS missing runtime-write services: {missing}"


# ==================================================================
# ALLOWLIST loader — parses the live file format correctly.
# ==================================================================


def test_load_allowlist_from_string(tmp_path: Path) -> None:
    p = tmp_path / "ALLOWLIST.md"
    p.write_text(
        "# ALLOWLIST\n"
        "\n"
        "## Allowed outbound integrations\n"
        "\n"
        "| Service | Auth path | Scope | Reason |\n"
        "|---|---|---|---|\n"
        "| **Slack** (workspace) | Bot tokens | scope | reason |\n"
        "| **Notion** | OAuth | read/write | reason |\n"
        "\n"
    )
    snap = R.load_allowlist(p)
    assert snap.parse_error is None
    assert "slack" in snap.services
    assert "notion" in snap.services


def test_load_allowlist_missing_file_yields_parse_error(tmp_path: Path) -> None:
    snap = R.load_allowlist(tmp_path / "nope.md")
    assert snap.parse_error is not None


# ==================================================================
# EXCEPTIONS loader — the machine block parses cleanly + version gate.
# ==================================================================


def test_load_exceptions_machine_block(tmp_path: Path) -> None:
    p = tmp_path / "EXCEPTIONS.md"
    p.write_text(
        "## Machine-parseable exceptions\n"
        "\n"
        "```yaml\n"
        "version: 1\n"
        "entries:\n"
        "  - class_id: google_calendar_write\n"
        '    scope: "interview events"\n'
        "    granted_date: 2026-06-30\n"
        '    review_condition: "scope broadens"\n'
        "    runtime_tool_class: google_calendar_write\n"
        "```\n"
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is None
    assert "google_calendar_write" in snap.entries


def test_load_exceptions_missing_version(tmp_path: Path) -> None:
    p = tmp_path / "EXCEPTIONS.md"
    p.write_text(
        "## Machine-parseable exceptions\n"
        "\n"
        "```yaml\n"
        "entries:\n"
        "  - class_id: x\n"
        "    runtime_tool_class: y\n"
        "```\n"
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is not None


def test_load_exceptions_missing_block(tmp_path: Path) -> None:
    p = tmp_path / "EXCEPTIONS.md"
    p.write_text("# No machine block here.\n")
    snap = R.load_exceptions(p)
    assert snap.parse_error is not None


# ==================================================================
# ALWAYS_ON loader — extracts labels from tables.
# ==================================================================


def test_load_always_on_extracts_labels(tmp_path: Path) -> None:
    p = tmp_path / "ALWAYS_ON.md"
    p.write_text(
        "## LaunchAgents\n"
        "\n"
        "| Label | What it does |\n"
        "|---|---|\n"
        "| `com.foo.bar` | test |\n"
        "| `com.foo.baz` | test |\n"
        "\n"
        "Plus a loose backticked mention of `ai.openclaw.gateway` in prose.\n"
    )
    snap = R.load_always_on(p)
    assert "com.foo.bar" in snap.labels
    assert "com.foo.baz" in snap.labels
    assert "ai.openclaw.gateway" in snap.labels


# ==================================================================
# Phase-2 PR #3: EXCEPTIONS.md v2 schema + agent_review_gate signoff.
# ==================================================================


def _write_exceptions(
    tmp_path: Path,
    *,
    version: str,
    entries_yaml: str,
) -> Path:
    p = tmp_path / "EXCEPTIONS.md"
    p.write_text(
        "## Machine-parseable exceptions\n"
        "\n"
        "```yaml\n"
        f"version: {version}\n"
        "entries:\n"
        f"{entries_yaml}"
        "```\n"
    )
    return p


def test_load_exceptions_v2_version_accepted(tmp_path: Path) -> None:
    """Phase-2 PR #3: v2 is a backward-compat extension of v1."""

    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: google_calendar_write\n"
            '    scope: "interview events"\n'
            "    granted_date: 2026-06-30\n"
            '    review_condition: "scope broadens"\n'
            "    runtime_tool_class: google_calendar_write\n"
        ),
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is None
    assert "google_calendar_write" in snap.entries


def test_load_exceptions_unsupported_version_is_parse_error(tmp_path: Path) -> None:
    """v3 (or any other) is NOT accepted — the loader knows only 1 and 2."""

    p = _write_exceptions(
        tmp_path,
        version="3",
        entries_yaml=(
            "  - class_id: some_class\n"
            "    runtime_tool_class: some_class\n"
        ),
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is not None
    assert "3" in snap.parse_error or "unsupported" in snap.parse_error.lower()


def test_load_exceptions_v1_entries_still_round_trip(tmp_path: Path) -> None:
    """v1 entries continue to parse into the runtime-tool-class index."""

    p = _write_exceptions(
        tmp_path,
        version="1",
        entries_yaml=(
            "  - class_id: notion_write\n"
            '    scope: "curated KB writes"\n'
            "    granted_date: 2026-01-15\n"
            '    review_condition: "scope broadens"\n'
            "    runtime_tool_class: notion_write\n"
        ),
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is None
    assert "notion_write" in snap.entries
    assert snap.entries["notion_write"]["scope"] == "curated KB writes"


def test_load_exceptions_all_entries_preserves_non_runtime_class(tmp_path: Path) -> None:
    """A `runtime_tool_class: n/a — not a runtime tool-call class` entry
    is filtered OUT of `entries` (runtime-index) but preserved in
    `all_entries` (so `load_agent_review_gate_signoff` can find it)."""

    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "gate-cleared PRs on auto-impl lane"\n'
            "    granted_date: 2026-07-15\n"
            '    review_condition: "gate quorum drops"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    approver: operator-handle\n"
            "    signoff_commit_sha: abc123def4567890abc123def4567890abc123de\n"
            '    signoff_pr_number: "42"\n'
        ),
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is None
    # Filtered out of the runtime-tool-class index.
    assert "n/a — not a runtime tool-call class" not in snap.entries
    assert "agent_auto_merge_code_review" not in snap.entries
    # But preserved in all_entries.
    class_ids = [e["class_id"] for e in snap.all_entries]
    assert "agent_auto_merge_code_review" in class_ids
    # And the v2 optional fields survived.
    entry = next(e for e in snap.all_entries if e["class_id"] == "agent_auto_merge_code_review")
    assert entry["approver"] == "operator-handle"
    assert entry["signoff_commit_sha"] == "abc123def4567890abc123def4567890abc123de"
    assert entry["signoff_pr_number"] == "42"


def test_load_exceptions_all_entries_includes_runtime_entries_too(tmp_path: Path) -> None:
    """`all_entries` is a superset of the runtime index — a runtime entry
    appears in BOTH `entries` and `all_entries`."""

    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: notion_write\n"
            '    scope: "curated KB writes"\n'
            "    granted_date: 2026-01-15\n"
            '    review_condition: "scope broadens"\n'
            "    runtime_tool_class: notion_write\n"
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "gate-cleared PRs"\n'
            "    granted_date: 2026-07-15\n"
            '    review_condition: "gate quorum drops"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
        ),
    )
    snap = R.load_exceptions(p)
    assert snap.parse_error is None
    assert "notion_write" in snap.entries
    class_ids = [e["class_id"] for e in snap.all_entries]
    assert set(class_ids) == {"notion_write", "agent_auto_merge_code_review"}


# ------------------------------------------------------------------
# load_agent_review_gate_signoff — sign-off accessor.
# ------------------------------------------------------------------


def test_load_agent_review_gate_signoff_returns_none_when_entry_absent(
    tmp_path: Path,
) -> None:
    """Absence of the entry is a distinct outcome from a present-but-
    unverifiable one — the PR #5 pre-merge check uses this shape to
    return `signoff_missing` vs `signoff_unverified`."""

    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: notion_write\n"
            '    scope: "curated KB writes"\n'
            "    granted_date: 2026-01-15\n"
            '    review_condition: "scope broadens"\n'
            "    runtime_tool_class: notion_write\n"
        ),
    )
    assert R.load_agent_review_gate_signoff(p) is None


def test_load_agent_review_gate_signoff_returns_none_on_parse_error(tmp_path: Path) -> None:
    """A malformed EXCEPTIONS.md returns None (not an exception) — the
    accessor never raises, so a consumer's happy path is untouched."""

    p = tmp_path / "EXCEPTIONS.md"
    p.write_text("# no machine block here\n")
    assert R.load_agent_review_gate_signoff(p) is None


def test_load_agent_review_gate_signoff_returns_block_with_v2_fields(
    tmp_path: Path,
) -> None:
    """A well-formed v2 entry returns a SignoffBlock with the optional
    fields populated."""

    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "gate-cleared PRs on auto-impl lane"\n'
            "    granted_date: 2026-07-15\n"
            '    review_condition: "gate quorum drops"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    approver: operator-handle\n"
            "    signoff_commit_sha: abc123def4567890abc123def4567890abc123de\n"
            '    signoff_pr_number: "42"\n'
        ),
    )
    block = R.load_agent_review_gate_signoff(p)
    assert block is not None
    assert block.class_id == "agent_auto_merge_code_review"
    assert block.scope == "gate-cleared PRs on auto-impl lane"
    assert block.granted_date == "2026-07-15"
    assert block.review_condition == "gate quorum drops"
    assert block.runtime_tool_class == "n/a — not a runtime tool-call class"
    assert block.approver == "operator-handle"
    assert block.signoff_commit_sha == "abc123def4567890abc123def4567890abc123de"
    assert block.signoff_pr_number == "42"


def test_load_agent_review_gate_signoff_v1_entry_has_none_v2_fields(tmp_path: Path) -> None:
    """A v1-shape entry (no optional fields) returns a SignoffBlock with
    None for each v2 field — the PR #5 verifier reads `None` as
    `signoff_provenance_unavailable` upstream."""

    # v1 doesn't have the sign-off block on the runtime-tool-class filter path,
    # so we author a v2 block with the entry but no optional fields.
    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "gate-cleared PRs on auto-impl lane"\n'
            "    granted_date: 2026-07-15\n"
            '    review_condition: "gate quorum drops"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
        ),
    )
    block = R.load_agent_review_gate_signoff(p)
    assert block is not None
    assert block.approver is None
    assert block.signoff_commit_sha is None
    assert block.signoff_pr_number is None


# -- lane_id selection: recovery (no-lane) + lane-scoped entries coexist -----


def _two_entry_exceptions(tmp_path: Path) -> Path:
    """A machine block with BOTH a lane-LESS recovery entry (#1707-shape) AND a
    lane-scoped rung-3 entry, each with a distinct signoff_commit_sha."""
    return _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "recovery-lane / generic (no lane)"\n'
            "    granted_date: 2026-07-13\n"
            '    review_condition: "x"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    signoff_commit_sha: reco0000000000000000000000000000000000000\n"
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "rung-3 agent-dispatch lane"\n'
            "    granted_date: 2026-08-12\n"
            '    review_condition: "y"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    lane_id: agent-dispatch-rung3\n"
            "    target_repo: operator-handle/agent-dispatch-sandbox\n"
            "    base_branch: main\n"
            "    merge_method: squash\n"
            "    actor: operator-handle\n"
            "    expires_at: 2999-01-01T00:00:00Z\n"
            "    signoff_commit_sha: lane0000000000000000000000000000000000000\n"
        ),
    )


def test_load_signoff_lane_none_returns_no_lane_recovery_entry(tmp_path: Path) -> None:
    # Default (recovery-lane) request resolves the lane-LESS entry — even though
    # a lane-scoped entry ALSO matches the class_id.
    block = R.load_agent_review_gate_signoff(_two_entry_exceptions(tmp_path))
    assert block is not None
    assert block.lane_id is None
    assert block.signoff_commit_sha == "reco0000000000000000000000000000000000000"


def test_load_signoff_lane_scoped_returns_matching_lane_entry(tmp_path: Path) -> None:
    block = R.load_agent_review_gate_signoff(
        _two_entry_exceptions(tmp_path), lane_id="agent-dispatch-rung3"
    )
    assert block is not None
    assert block.lane_id == "agent-dispatch-rung3"
    assert block.target_repo == "operator-handle/agent-dispatch-sandbox"
    assert block.signoff_commit_sha == "lane0000000000000000000000000000000000000"


# -- H1 list-field parsing: inline-list fence_globs / permitted_item_classes ---
# Regression for the arming blocker (2026-08-13): the hand-written machine-block
# parser stores every value as a STRING, so an inline list `[src/**, tests/**]`
# arrived as the string "[src/**, tests/**]" and _str_tuple returned () → a
# lane-scoped sign-off loaded with an EMPTY envelope → verify_lane_scope
# fail-closed `fence_globs_absent` (the lane could never merge).


def _rung3_signoff_with_lists(tmp_path: Path, *, fence: str, classes: str) -> Path:
    return _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "rung-3 lane"\n'
            "    granted_date: 2026-08-13\n"
            '    review_condition: "y"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    lane_id: agent-dispatch-rung3\n"
            "    target_repo: operator-handle/agent-dispatch-sandbox\n"
            "    base_branch: main\n"
            f"    fence_globs: {fence}\n"
            f"    permitted_item_classes: {classes}\n"
            "    merge_method: squash\n"
            "    actor: operator-handle\n"
            "    expires_at: 2999-01-01T00:00:00Z\n"
            "    signoff_pr_number: 4242\n"
            "    signoff_commit_sha: lane0000000000000000000000000000000000000\n"
        ),
    )


def test_load_signoff_parses_inline_list_fence_and_classes(tmp_path: Path) -> None:
    block = R.load_agent_review_gate_signoff(
        _rung3_signoff_with_lists(tmp_path, fence="[src/**, tests/**]", classes="[impl]"),
        lane_id="agent-dispatch-rung3",
    )
    assert block is not None
    assert block.fence_globs == ("src/**", "tests/**")
    assert block.permitted_item_classes == ("impl",)


def test_load_signoff_parses_quoted_inline_list(tmp_path: Path) -> None:
    block = R.load_agent_review_gate_signoff(
        _rung3_signoff_with_lists(tmp_path, fence="['src/**', 'tests/**']", classes="[impl]"),
        lane_id="agent-dispatch-rung3",
    )
    assert block is not None
    assert block.fence_globs == ("src/**", "tests/**")


def test_load_signoff_empty_inline_list_is_empty_tuple(tmp_path: Path) -> None:
    block = R.load_agent_review_gate_signoff(
        _rung3_signoff_with_lists(tmp_path, fence="[]", classes="[]"),
        lane_id="agent-dispatch-rung3",
    )
    assert block is not None
    assert block.fence_globs == ()
    assert block.permitted_item_classes == ()


def test_load_signoff_lane_none_returns_none_when_only_lane_entries(tmp_path: Path) -> None:
    # A lane-scoped-only block → a recovery (lane_id=None) request gets None
    # (signoff_missing), never a lane entry.
    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "rung-3 only"\n'
            "    granted_date: 2026-08-12\n"
            '    review_condition: "y"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    lane_id: agent-dispatch-rung3\n"
        ),
    )
    assert R.load_agent_review_gate_signoff(p) is None


def test_load_signoff_lane_scoped_returns_none_when_lane_absent(tmp_path: Path) -> None:
    # Request a lane whose row isn't authored yet → None (signoff_missing).
    assert (
        R.load_agent_review_gate_signoff(
            _two_entry_exceptions(tmp_path), lane_id="some-other-lane"
        )
        is None
    )


def test_load_signoff_lane_none_order_independent(tmp_path: Path) -> None:
    # Lane entry authored FIRST in the file → a recovery (None) request STILL
    # resolves the lane-less entry (selection is by lane-absence, not order).
    p = _write_exceptions(
        tmp_path,
        version="2",
        entries_yaml=(
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "rung-3 lane (authored first)"\n'
            "    granted_date: 2026-08-12\n"
            '    review_condition: "y"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    lane_id: agent-dispatch-rung3\n"
            "    signoff_commit_sha: lane0000000000000000000000000000000000000\n"
            "  - class_id: agent_auto_merge_code_review\n"
            '    scope: "recovery (authored second)"\n'
            "    granted_date: 2026-07-13\n"
            '    review_condition: "x"\n'
            '    runtime_tool_class: "n/a — not a runtime tool-call class"\n'
            "    signoff_commit_sha: reco0000000000000000000000000000000000000\n"
        ),
    )
    block = R.load_agent_review_gate_signoff(p)
    assert block is not None and block.lane_id is None
    assert block.signoff_commit_sha == "reco0000000000000000000000000000000000000"


def test_signoff_class_id_constant_matches_dataclass(tmp_path: Path) -> None:
    """The exported class-id constant matches what
    `load_agent_review_gate_signoff` searches for — a single source of
    truth for the phase-2 sign-off block name."""

    assert R.AGENT_REVIEW_GATE_SIGNOFF_CLASS_ID == "agent_auto_merge_code_review"
