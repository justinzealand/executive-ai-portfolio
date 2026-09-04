#!/usr/bin/env python3
# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Repo-scope guard — detect the "wrong repo" showstopper before work proceeds.

The core hazard (observed 2026-06-21): the shared ``<workspace>``
(``projects-workspace``) **primary** checkout is parked on a non-``main`` feature
branch. Because every session that starts at ``<workspace>`` shares that one
working tree, a parked feature branch:

  - blocks sibling sessions from committing workspace changes to ``main``,
  - makes ``/ship`` target the wrong branch,
  - accumulates unrelated workstreams into one un-shippable "kitchen-sink" branch.

This module is the single source of truth consumed by:

  - the **CC** hook dispatcher (``claude-code-hooks/dispatcher.py``) — SessionStart
    banner + PreToolUse hard-deny of mutations that target the parked ROOT repo;
  - the **Codex** hook dispatcher (``~/.codex/hooks/dispatcher.py``) — same, mirrored;
  - **OpenClaw** ship/commit-equivalent skills — advisory (OC has no hard-block hook).

It is **FAIL-OPEN**: any git/IO error yields *no* block and *no* showstopper, so a
guard bug can never wedge a session.

False-positive avoidance (the load-bearing distinctions):

  - A linked **worktree** of ``projects-workspace`` (``toplevel != <workspace>``) is
    the sanctioned way to do branch work → never blocked.
  - A **sub-repo** (``delegated-work`` etc.; ``toplevel == <workspace>/<sub>``) is
    normal per-repo branch work → never blocked.
  - Only the **primary** ``<workspace>`` checkout being on a non-``main`` branch trips
    the guard, and only mutations whose target resolves to that primary checkout
    are blocked.
"""
from __future__ import annotations
import os

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

WORKSPACE_ROOT = Path(
    os.environ.get("WORKSPACE_ROOT", str(Path.home() / "projects"))
)
MAIN_BRANCH = "main"
# Current repo more than this many commits behind origin/main -> advisory warn.
DIVERGENCE_WARN_THRESHOLD = 10
_GIT_TIMEOUT = 5


def _git(args: list[str], cwd: Path | str) -> str | None:
    """Run a git command; return stripped stdout, or None on any failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def repo_toplevel(path: Path | str) -> str | None:
    """git working-tree toplevel containing ``path`` (a worktree returns its own dir)."""
    p = Path(path)
    base = p if p.is_dir() else p.parent
    # A mutation target may be a NEW file in a not-yet-existing dir; walk up to
    # the nearest existing ancestor so git can resolve the enclosing repo.
    while not base.is_dir() and base != base.parent:
        base = base.parent
    return _git(["rev-parse", "--show-toplevel"], base)


def current_branch(cwd: Path | str) -> str | None:
    b = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    # Detached HEAD reports "HEAD"; treat as unknown (fail-open, not parked).
    if b in (None, "HEAD"):
        return None
    return b


def primary_root_branch(root: Path = WORKSPACE_ROOT) -> str | None:
    """Branch of the **primary** projects-workspace checkout at ``root``.

    Returns None (fail-open) when ``root`` is not the primary toplevel or git errors.
    """
    tl = repo_toplevel(root)
    if tl is None or Path(tl) != Path(root):
        return None
    return current_branch(root)


def is_root_parked(root: Path = WORKSPACE_ROOT) -> bool:
    """True iff the primary ``<workspace>`` checkout is on a non-``main`` branch."""
    b = primary_root_branch(root)
    return bool(b) and b != MAIN_BRANCH


def path_targets_primary_root(path: Path | str, root: Path = WORKSPACE_ROOT) -> bool:
    """True iff ``path`` lives in the **primary** root checkout (not a worktree/sub-repo)."""
    tl = repo_toplevel(path)
    return tl is not None and Path(tl) == Path(root)


def path_is_git_ignored(path: Path | str, root: Path = WORKSPACE_ROOT) -> bool:
    """True iff ``path`` is **git-ignored** within the primary root checkout.

    The read-only-root model forbids authoring *root-tracked* files in the primary
    checkout, NOT all filesystem writes under it. The CG-6 invariant states it plainly:
    "The primary checkout stays clean on ``main``, and NO root-tracked files are authored
    there. Ignored operational dirs (``.claude/worktrees/``) MAY be created/removed under
    the primary checkout." A git-ignored scratch path (``.loose-ends-for-document/…``,
    ``.claude/worktrees/…``) is operational state, not a tracked edit — so authoring it is
    permitted. This helper is the precise discriminator the over-broad block was missing.

    ``git check-ignore`` exit codes are distinguished explicitly (the module-wide ``_git``
    helper conflates "exit 1 = not ignored" with "error"): returncode 0 ⇒ ignored ⇒ True;
    returncode 1 ⇒ NOT ignored ⇒ False; any other code (128, IO/timeout) ⇒ error ⇒ False.

    Fail direction is **toward False** (i.e. "treat as NOT ignored ⇒ keep blocking"). This is
    the conservative direction for THIS sub-decision and deliberately the *opposite* of the
    module's overall fail-OPEN posture: a guard bug here must never silently widen the
    read-only root to admit a TRACKED write. The close-chain scratch paths are reliably
    ignored, so ``check-ignore`` does not error on them in practice; a transient git error
    only costs a (visible, retryable) block of a scratch write, never a silent tracked leak.
    """
    # ``check-ignore`` resolves the path against ``-C root``; an absolute target works
    # whether or not it (or its parent dir) exists yet, so a not-yet-created scratch file
    # under an ignored dir still classifies as ignored.
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", str(Path(path))],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except Exception:
        return False
    return out.returncode == 0


def behind_origin_main(cwd: Path | str) -> int | None:
    """How many commits ``cwd``'s repo is behind ``origin/main`` (None on error)."""
    cnt = _git(["rev-list", "--count", "HEAD..origin/main"], cwd)
    if cnt is None:
        return None
    try:
        return int(cnt)
    except ValueError:
        return None


def describe_target(path: Path | str) -> str:
    """One-line ``repo: <name> branch: <branch>`` for surfacing on ship/push."""
    tl = repo_toplevel(path)
    if tl is None:
        return "repo: <none> branch: <none>"
    return f"repo: {Path(tl).name} branch: {current_branch(tl) or '<detached>'}"


@dataclass
class GuardResult:
    block: bool
    showstopper: bool
    reason: str
    resolution: str
    divergence_warning: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# Hint appended to every read-only-root deny so an agent who is LEGITIMATELY in a
# worktree but got blocked here knows the one-line workaround. The recurring
# false-positive (bit ~3x in one session, 2026-06-25): a mutator whose worktree
# target is passed as a shell VARIABLE (`git -C "$VAR" …` / `cd "$VAR" && git …`).
# The hook reads the Bash command BEFORE shell expansion, can't resolve the var, and
# falls back to the anchor cwd (<workspace>) — so a real worktree commit reads as a
# read-only-root violation. The structural cure is to re-issue with the LITERAL path.
# See memory feedback_readonly_root_literal_paths_in_git_c.md.
_WORKTREE_LITERAL_PATH_HINT = (
    "  IF YOU ARE ALREADY IN A WORKTREE and got blocked here, you most likely passed the\n"
    "  worktree path as a shell VARIABLE — `git -C \"$VAR\" …` or `cd \"$VAR\" && git …`. The\n"
    "  hook reads the command BEFORE shell expansion, cannot resolve the variable, and fell\n"
    "  back to the anchor cwd (~/projects). Re-run with the LITERAL worktree path (no $vars):\n"
    "    git -C ~/projects/.claude/worktrees/<name> commit -m \"…\""
)


def _resolution(branch: str) -> str:
    return (
        f"ROOT ~/projects (projects-workspace) is parked on '{branch}', not '{MAIN_BRANCH}'.\n"
        "  RESOLVE before any workspace-root work:\n"
        "    - return the root to main:   git -C ~/projects switch main\n"
        "    - OR do a root edit without un-parking, AGENT (CC or Codex): use the helper —\n"
        "        python3 ~/projects/scripts/agent-worktrees/wsroot_helper.py create --owner claude|codex --slug <slug>\n"
        "        → edit in the printed worktree → `… ship --worktree <path> --path <file> -m \"<msg>\"`\n"
        "    - OR do branch work in a worktree:\n"
        "        git -C ~/projects worktree add -b <branch> /tmp/<name> origin/main\n"
        "  Sub-repo work (cd into a sub-repo) and existing worktrees are unaffected.\n"
        + _WORKTREE_LITERAL_PATH_HINT
    )


def _divergence_warning(cwd: Path | str) -> str | None:
    div = behind_origin_main(cwd)
    if div is not None and div > DIVERGENCE_WARN_THRESHOLD:
        tl = repo_toplevel(cwd)
        name = Path(tl).name if tl else str(cwd)
        return (
            f"DIVERGENCE: {name} is {div} commits behind origin/main "
            f"(> {DIVERGENCE_WARN_THRESHOLD}) — rebase/refresh before building on it."
        )
    return None


def evaluate_session_start(
    cwd: Path | str = WORKSPACE_ROOT, root: Path = WORKSPACE_ROOT
) -> GuardResult:
    """SessionStart banner: showstopper iff the primary root is parked off main."""
    branch = primary_root_branch(root)
    div_warn = _divergence_warning(cwd)
    if bool(branch) and branch != MAIN_BRANCH:
        return GuardResult(
            block=False,
            showstopper=True,
            reason=f"Root projects-workspace is parked on '{branch}'.",
            resolution=_resolution(branch),
            divergence_warning=div_warn,
        )
    return GuardResult(
        block=False,
        showstopper=False,
        reason="Root projects-workspace is on main.",
        resolution="",
        divergence_warning=div_warn,
    )


def bare_root_nudge(cwd: Path | str = WORKSPACE_ROOT, root: Path = WORKSPACE_ROOT) -> str | None:
    """Advisory nudge when a session opens in the BARE workspace root.

    The root is for cross-cutting workspace files only (scripts/, docs, CLAUDE.md).
    Repo-specific work done here parks or dirties the shared checkout that the CLI
    AND launchd crons execute from — the recurring trouble source (sessions default
    their cwd to <workspace> regardless of which repo they're actually working on).
    Returns a one-line reminder when cwd IS exactly the bare root, else None. Pure,
    fail-open (None on error). NOT a showstopper — a parked root already emits the
    stronger showstopper banner; this only nudges an on-main bare-root session.
    """
    try:
        if Path(cwd).expanduser().resolve() == Path(root).expanduser().resolve():
            return (
                "Workspace-root reminder: you're in the shared ~/projects root, which is "
                "for cross-cutting workspace files only. For repo-specific work, cd into the "
                "target sub-repo (cd ~/projects/<repo>) — don't park the root on a feature "
                "branch or leave it dirty; the CLI and launchd crons run whatever is checked "
                "out here."
            )
    except Exception:
        return None
    return None


def _readonly_resolution() -> str:
    return (
        "ROOT ~/projects (projects-workspace) is READ-ONLY by construction — it stays on a\n"
        "  clean 'main' and is the live surface the CLI + launchd crons run from.\n"
        "  Author workspace-root edits in a worktree, never the primary checkout:\n"
        "    - AGENT (CC or Codex, no interactive launch): use the helper —\n"
        "        python3 ~/projects/scripts/agent-worktrees/wsroot_helper.py create --owner claude|codex --slug <slug>\n"
        "        → edit in the printed worktree → `… ship --worktree <path> --path <file> -m \"<msg>\"`\n"
        "        (commits only named paths, never -A; PR-only; ff anchor after a confirmed merge).\n"
        "    - HUMAN shell: `wsroot [slug]` → edit → `wsship \"<commit msg>\"` (PR-only) to land on main.\n"
        "  Sub-repo work: cd ~/projects/<repo> (its own repo — never blocked).\n"
        "  Existing worktrees (~/projects/.claude/worktrees/…) are never blocked.\n"
        + _WORKTREE_LITERAL_PATH_HINT
    )


def evaluate_mutation(
    target_path: Path | str,
    root: Path = WORKSPACE_ROOT,
    *,
    always: bool = False,
    allow_ignored: bool = True,
) -> GuardResult:
    """PreToolUse: hard-block a mutation targeting the **primary** root checkout.

    With ``always=False`` (default, backward-compatible) the block fires only when the
    root is parked off ``main``. With ``always=True`` the primary checkout is treated as
    READ-ONLY by construction and ANY mutation targeting it is blocked (the read-only-root
    model), with the parked-specific message still preferred when the root happens to be
    parked. Worktree and sub-repo mutations are always allowed (block=False). Fail-open:
    a worktree/sub-repo target — or a target git cannot resolve — never blocks.

    ``allow_ignored`` (default True) implements the CG-6 carve-out: a **git-ignored** path
    under the primary root is operational scratch (``.loose-ends-for-document/…``,
    ``.claude/worktrees/…``), NOT a root-tracked file, so authoring it is permitted even
    under the read-only-root model — without it the close chain's own gitignored anchor
    writes were over-blocked (observed 2026-06-25). Callers that target the *repository*
    rather than a single file — e.g. a ``git commit``/``reset`` Bash mutator, where a
    git-ignored ``-C``/``cd`` subdir would still commit to the root repo — MUST pass
    ``allow_ignored=False`` so the carve-out cannot launder a tree/branch mutation through
    an ignored directory.
    """
    parked = is_root_parked(root)
    if not always and not parked:
        return GuardResult(False, False, "Root on main; no block.", "")
    if path_targets_primary_root(target_path, root):
        if allow_ignored and path_is_git_ignored(target_path, root):
            return GuardResult(
                False,
                False,
                "Mutation targets a git-ignored operational path under the root (allowed).",
                "",
            )
        if parked:
            branch = primary_root_branch(root)
            return GuardResult(
                block=True,
                showstopper=True,
                reason=(
                    "BLOCKED: this mutation targets the projects-workspace ROOT checkout "
                    f"while it is parked on '{branch}'. Workspace edits would land on the "
                    "wrong branch and collide with the parked session."
                ),
                resolution=_resolution(branch),
            )
        return GuardResult(
            block=True,
            showstopper=True,
            reason=(
                "BLOCKED: this mutation targets the projects-workspace ROOT checkout, "
                "which is read-only by construction. Author root edits via `wsroot`."
            ),
            resolution=_readonly_resolution(),
        )
    return GuardResult(
        False,
        False,
        "Mutation targets a worktree or sub-repo (allowed).",
        "",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repo-scope showstopper guard.")
    ap.add_argument("--mode", choices=["session-start", "mutation", "describe"], default="session-start")
    ap.add_argument("--cwd", default=str(WORKSPACE_ROOT), help="cwd for the check")
    ap.add_argument("--target", default=None, help="mutation target path (mutation mode)")
    ap.add_argument("--root", default=str(WORKSPACE_ROOT), help="workspace root override")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.mode == "describe":
        print(describe_target(args.target or args.cwd))
        return 0
    if args.mode == "mutation":
        res = evaluate_mutation(args.target or args.cwd, root=root)
    else:
        res = evaluate_session_start(args.cwd, root=root)

    if args.json:
        print(res.to_json())
    else:
        if res.showstopper:
            print(f"SHOWSTOPPER: {res.reason}")
            print(res.resolution)
        else:
            print(res.reason)
        if res.divergence_warning:
            print(res.divergence_warning)
    # Exit 2 signals "block" to a shell caller; 0 otherwise. Dispatchers import
    # the functions directly and do not rely on this code.
    return 2 if res.block else 0


if __name__ == "__main__":
    raise SystemExit(main())
