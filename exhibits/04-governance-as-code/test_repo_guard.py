# Exhibit copy from a private workspace (snapshot 2026-09-04). Paths generalized; behavior unchanged.
"""Tests for repo_guard — the repo-scope showstopper guard.

Covers the load-bearing distinctions: primary-root-parked vs worktree vs sub-repo,
the divergence warning, and fail-open behaviour on git errors.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import repo_guard as rg  # noqa: E402


def _git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _init_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", branch], path)
    _git(["config", "user.email", "t@t.t"], path)
    _git(["config", "user.name", "t"], path)
    (path / "f.txt").write_text("x")
    _git(["add", "."], path)
    _git(["commit", "-q", "-m", "init"], path)
    return path


# --- primary root branch detection -------------------------------------------

def test_root_on_main_not_parked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    assert rg.primary_root_branch(root) == "main"
    assert rg.is_root_parked(root) is False


def test_root_on_feature_is_parked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "codex/feature"], root)
    assert rg.primary_root_branch(root) == "codex/feature"
    assert rg.is_root_parked(root) is True


def test_detached_head_is_fail_open_not_parked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                         capture_output=True, text=True).stdout.strip()
    _git(["checkout", "-q", sha], root)  # detached
    assert rg.primary_root_branch(root) is None  # HEAD -> None
    assert rg.is_root_parked(root) is False


def test_nonexistent_root_fail_open(tmp_path):
    assert rg.primary_root_branch(tmp_path / "nope") is None
    assert rg.is_root_parked(tmp_path / "nope") is False


# --- session-start evaluation -------------------------------------------------

def test_session_start_on_main_no_showstopper(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    res = rg.evaluate_session_start(cwd=root, root=root)
    assert res.showstopper is False and res.block is False


def test_session_start_parked_is_showstopper(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "kitchen-sink"], root)
    res = rg.evaluate_session_start(cwd=root, root=root)
    assert res.showstopper is True and res.block is False
    assert "kitchen-sink" in res.reason
    assert "git -C ~/projects switch main" in res.resolution


# --- mutation evaluation: the primary/worktree/subrepo distinction ------------

def test_mutation_targeting_parked_root_is_blocked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "feature"], root)
    res = rg.evaluate_mutation(root / "scripts" / "x.py", root=root)
    assert res.block is True and res.showstopper is True


def test_mutation_targeting_root_when_on_main_allowed(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    res = rg.evaluate_mutation(root / "scripts" / "x.py", root=root)
    assert res.block is False


def test_mutation_targeting_subrepo_allowed_even_when_root_parked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    sub = _init_repo(root / "delegated-work", "main")  # nested independent repo
    _git(["switch", "-c", "feature"], root)
    res = rg.evaluate_mutation(sub / "index.js", root=root)
    assert res.block is False  # sub-repo work always flows
    assert rg.path_targets_primary_root(sub / "index.js", root=root) is False


def test_mutation_targeting_worktree_allowed_even_when_root_parked(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "feature"], root)
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(wt), "main"], root)
    res = rg.evaluate_mutation(wt / "scripts" / "x.py", root=root)
    assert res.block is False  # worktree is the sanctioned branch-work path
    assert rg.path_targets_primary_root(wt / "scripts" / "x.py", root=root) is False


# --- divergence warning -------------------------------------------------------

def test_divergence_warning_emitted_over_threshold(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "projects", "main")
    monkeypatch.setattr(rg, "behind_origin_main", lambda cwd: 25)
    res = rg.evaluate_session_start(cwd=root, root=root)
    assert res.divergence_warning is not None and "25 commits behind" in res.divergence_warning


def test_no_divergence_warning_under_threshold(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "projects", "main")
    monkeypatch.setattr(rg, "behind_origin_main", lambda cwd: 3)
    res = rg.evaluate_session_start(cwd=root, root=root)
    assert res.divergence_warning is None


def test_behind_origin_main_none_without_remote(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    assert rg.behind_origin_main(root) is None  # no origin/main -> fail-open None


# --- describe_target ----------------------------------------------------------

def test_describe_target(tmp_path):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "feat"], root)
    desc = rg.describe_target(root / "f.txt")
    assert "repo: projects" in desc and "branch: feat" in desc


def test_describe_target_non_repo(tmp_path):
    assert "none" in rg.describe_target(tmp_path / "nope" / "f")


# --- CLI ----------------------------------------------------------------------

def test_cli_mutation_blocked_exit2(tmp_path, capsys):
    root = _init_repo(tmp_path / "projects", "main")
    _git(["switch", "-c", "feature"], root)
    rc = rg.main(["--mode", "mutation", "--root", str(root),
                  "--target", str(root / "x.py"), "--json"])
    assert rc == 2
    assert '"block": true' in capsys.readouterr().out


def test_bare_root_nudge_fires_only_at_the_bare_root(tmp_path):
    root = tmp_path / "projects"
    (root / "lexicon-ios").mkdir(parents=True)
    # cwd == bare root → nudge.
    msg = rg.bare_root_nudge(root, root=root)
    assert msg and "shared ~/projects root" in msg and "cd into the" in msg
    # cwd inside a sub-repo → no nudge.
    assert rg.bare_root_nudge(root / "lexicon-ios", root=root) is None
    # unrelated cwd → no nudge.
    assert rg.bare_root_nudge(tmp_path / "elsewhere", root=root) is None
