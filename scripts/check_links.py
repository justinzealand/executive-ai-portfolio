#!/usr/bin/env python3
"""Verify every relative Markdown link and image in the tree resolves to a file or directory."""
from __future__ import annotations

import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
REFERENCE_LINK = re.compile(r"!?\[([^\]]+)\]\[([^\]]*)\]")
REFERENCE_DEFINITION = re.compile(r"^\s*\[([^\]]+)\]:\s*(\S+)")


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-z]+://|^mailto:", target))


def markdown_targets(lines: list[str]) -> list[tuple[int, str]]:
    """Return local inline and reference-style link targets with line numbers."""
    definitions: dict[str, str] = {}
    for line in lines:
        match = REFERENCE_DEFINITION.match(line)
        if match:
            definitions[match.group(1).strip().casefold()] = match.group(2)

    targets: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, 1):
        targets.extend((line_number, target) for target in INLINE_LINK.findall(line))
        for text, label in REFERENCE_LINK.findall(line):
            reference = (label or text).strip().casefold()
            target = definitions.get(reference)
            if target:
                targets.append((line_number, target.split("#", 1)[0]))
    return targets


def main() -> int:
    bad = 0
    for markdown_file in sorted(ROOT.rglob("*.md")):
        if ".git" in markdown_file.parts:
            continue
        lines = markdown_file.read_text(encoding="utf-8").splitlines()
        for line_number, target in markdown_targets(lines):
            if not target or is_external(target):
                continue
            if not (markdown_file.parent / target).exists():
                bad += 1
                relative = markdown_file.relative_to(ROOT)
                print(f"{relative}:{line_number}: missing {target}")
    print("links ok" if not bad else f"{bad} broken link(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
