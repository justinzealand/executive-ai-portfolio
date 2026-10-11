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
SKIP_DIRS = {".git", ".playwright-cli", "__pycache__", ".pytest_cache"}
ALLOW_FILES = {
    "scripts/check_public_safety.py",
    "scripts/check_site.py",
    "site-src/assets/headshot.jpeg",
    "site-src/assets/headshot-justin-zealand.jpg",
    "site-src/assets/justin-zealand-resume.pdf",
    "site-src/assets/zealand-technology-logo.png",
    "site-src/assets/zealand-technology-text.png",
    "dist/assets/headshot.jpeg",
    "dist/assets/headshot-justin-zealand.jpg",
    "dist/assets/justin-zealand-resume.pdf",
    "dist/assets/zealand-technology-logo.png",
    "dist/assets/zealand-technology-text.png",
}
# Literals reviewed by hand and accepted (public URL; test fixture with no real path).
ALLOWED_LITERALS = (
    "linkedin.com/in/justinzealand",
    "github.com/justinzealand/executive-ai-portfolio",
    "portfolio.zealand.technology",
    "zealand.technology",
    "zealandtech@gmail.com",
    '"mv ~/foo.plist ~/tmp/"',
)


def allowed_match(line: str, match: re.Match[str]) -> bool:
    """Allow only the reviewed literal itself, never unrelated text on its line."""
    for literal in ALLOWED_LITERALS:
        start = line.find(literal)
        while start >= 0:
            if start <= match.start() and match.end() <= start + len(literal):
                return True
            start = line.find(literal, start + 1)
    return False


def svg_css_selector(line: str) -> bool:
    """Avoid treating an SVG CSS selector as a Slack channel name."""
    return bool(re.search(r"(?:^|[;{}\s])[#.][a-z][a-z0-9_-]*\s*(?:[,{])", line, re.I))


def css_color(file: pathlib.Path, match: re.Match[str]) -> bool:
    return file.suffix == ".css" and bool(re.fullmatch(r"#[0-9a-f]{3,8}", match.group(0), re.I))


def html_fragment(file: pathlib.Path, line: str, match: re.Match[str]) -> bool:
    if file.suffix != ".html":
        return False
    return f'href="{match.group(0)}"' in line or f"href='{match.group(0)}'" in line

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
            hits += 1
            print(f"{rel}: [non-utf-8] file was not scanned")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in PATTERNS.items():
                if name == "slack channel name" and f.suffix == ".svg" and svg_css_selector(line):
                    continue  # CSS id selectors (#my-svg) are not channel names
                for m in rx.finditer(line):
                    if name == "slack channel name" and (css_color(f, m) or html_fragment(f, line, m)):
                        continue
                    if allowed_match(line, m):
                        continue
                    hits += 1
                    print(f"{rel}:{n}: [{name}] {m.group(0)!r}")
    print(f"\n{hits} finding(s)" if hits else "clean: 0 findings")
    return 1 if hits else 0

if __name__ == "__main__":
    sys.exit(main())
