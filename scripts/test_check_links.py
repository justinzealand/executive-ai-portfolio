"""Regression tests for local Markdown link validation."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys


SCRIPT = pathlib.Path(__file__).with_name("check_links.py")


def run_check(tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    return subprocess.run(
        [sys.executable, str(scripts / SCRIPT.name)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )


def test_reference_link_to_missing_file_fails(tmp_path: pathlib.Path) -> None:
    (tmp_path / "README.md").write_text(
        "Read [the missing guide][guide].\n\n[guide]: docs/missing.md\n",
        encoding="utf-8",
    )

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "README.md:1: missing docs/missing.md" in result.stdout


def test_reference_link_to_existing_file_passes(tmp_path: pathlib.Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("Guide\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "Read [the guide][guide].\n\n[guide]: docs/guide.md\n",
        encoding="utf-8",
    )

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert result.stdout == "links ok\n"
