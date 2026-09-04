#!/usr/bin/env python3
"""Verify every relative Markdown link and image in the tree resolves to a file or directory."""
import re, sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
rx = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
bad = 0
for md in sorted(root.rglob("*.md")):
    if ".git" in md.parts: continue
    for n, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        for target in rx.findall(line):
            if re.match(r"^[a-z]+://|^mailto:", target): continue
            if not (md.parent / target).exists():
                bad += 1; print(f"{md.relative_to(root)}:{n}: missing {target}")
print("links ok" if not bad else f"{bad} broken link(s)"); sys.exit(1 if bad else 0)
