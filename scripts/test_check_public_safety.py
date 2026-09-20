"""Regression tests for the public-safety scanner's exclusion boundaries."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys


SCRIPT = pathlib.Path(__file__).with_name("check_public_safety.py")


def run_scan(tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
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


def test_allowed_literal_does_not_hide_another_match(tmp_path: pathlib.Path) -> None:
    (tmp_path / "sample.txt").write_text(
        "https://linkedin.com/in/justin" "zealand /" "Users/private-user\n",
        encoding="utf-8",
    )

    result = run_scan(tmp_path)

    assert result.returncode == 1
    assert "[home path]" in result.stdout


def test_non_utf8_file_fails_closed(tmp_path: pathlib.Path) -> None:
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\x00")

    result = run_scan(tmp_path)

    assert result.returncode == 1
    assert "binary.dat: [non-utf-8] file was not scanned" in result.stdout


def test_svg_css_selector_does_not_hide_a_channel_name(tmp_path: pathlib.Path) -> None:
    (tmp_path / "diagram.svg").write_text(
        "#" "diagram { fill: red; }\n" "#" "ops" "-alerts\n",
        encoding="utf-8",
    )

    result = run_scan(tmp_path)

    assert result.returncode == 1
    assert "[slack channel name] '" + "#" "ops-alerts'" in result.stdout


def test_ticket_price_tracker_slug_is_detected(tmp_path: pathlib.Path) -> None:
    slug = "ticket-price" "-tracker"
    (tmp_path / "sample.md").write_text(f"{slug}\n", encoding="utf-8")

    result = run_scan(tmp_path)

    assert result.returncode == 1
    assert "[personal domain] 'ticket-price" "-tracker'" in result.stdout
