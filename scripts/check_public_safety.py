#!/usr/bin/env python3
"""Public-safety gate for the executive-ai-portfolio staging tree.

Exit 1 if any file contains a pattern that must never reach a public repo:
local filesystem paths, private hostnames, network identifiers, messaging
channel ids, personal contacts, credential-store references, or personal-domain
product names. Run from the repository root: python3 scripts/check_public_safety.py
"""
from __future__ import annotations
import re, sys, pathlib

PATTERNS = {
    # Absolute, user-identifying paths. Tilde paths under standard tool/config dirs
    # (~/projects, ~/.claude, ~/Library/LaunchAgents) are generic layout, allowed.
    "home path": re.compile(r"/Users/|/home/[a-z]+/|(?<![\w`<])~/(?!projects|\.claude|\.codex|\.openclaw|\.agents|\.automation-state|Library/|tmp)[\w.\-]"),
    # Real tailnet hostnames only; the generic MagicDNS suffix is public vocabulary.
    "tailnet host": re.compile(r"tailad\d+|mac-mini|[a-z0-9-]+\.tail[a-z0-9]+\.ts\.net", re.I),
    "ipv4 (non-loopback)": re.compile(r"\b(?!127\.0\.0\.1)(?!0\.0\.0\.0)\d{1,3}(?:\.\d{1,3}){3}\b"),
    "slack channel id": re.compile(r"\bC0[A-Z0-9]{8,}\b"),
    "slack channel name": re.compile(r"(?<![\w/])#[a-z][a-z0-9\-]{3,}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}(?![\w.])"),
    "phone": re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
    "notion / artifact url": re.compile(r"notion\.(so|com)/\S|app\.notion|claude\.ai/code", re.I),
    "credential store": re.compile(r"1password|keychain|find-generic-password|oauth[-_ ]token", re.I),
    "personal domain": re.compile(r"wolf ?trap|vividseats|quicken|fcps|single father|telegram chat id|justinzealand|zealandtech", re.I),
    "session id": re.compile(r"\b(?!abc12345-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    "secret-like": re.compile(r"xox[abp]-|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"),
}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
ALLOW_FILES = {"scripts/check_public_safety.py", "SAFETY-INVENTORY.md"}  # inventory is private staging, removed before publication
# Literals reviewed by hand and accepted (public URL; test fixture with no real path).
ALLOWED_LITERALS = ("linkedin.com/in/justinzealand", "github.com/justinzealand/executive-ai-portfolio", '"mv ~/foo.plist ~/tmp/"')

def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = 0
    for f in sorted(root.rglob("*")):
        if not f.is_file() or any(p in SKIP_DIRS for p in f.parts):
            continue
        rel = f.relative_to(root).as_posix()
        if rel in ALLOW_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(lit in line for lit in ALLOWED_LITERALS):
                continue
            for name, rx in PATTERNS.items():
                m = rx.search(line)
                if m:
                    hits += 1
                    print(f"{rel}:{n}: [{name}] {line.strip()[:140]}")
    print(f"\n{hits} finding(s)" if hits else "clean: 0 findings")
    return 1 if hits else 0

if __name__ == "__main__":
    sys.exit(main())
