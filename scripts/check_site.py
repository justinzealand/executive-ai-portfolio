#!/usr/bin/env python3
"""Validate the generated portfolio's links, structure, and public boundary."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXPECTED_PAGES = {
    "index.html",
    "experience/index.html",
    "case-studies/index.html",
    "case-studies/operating-model/index.html",
    "case-studies/proportional-governance/index.html",
    "case-studies/trustworthy-ai-work/index.html",
    "leadership/index.html",
    "resume/index.html",
    "contact/index.html",
}
PRIVATE_PATTERNS = {
    "local path": re.compile(r"/Users/|/home/|Downloads iCloud|Mobile Documents", re.I),
    "private source name": re.compile(r"Resume Metrics|Target Profile|Evidence Dossier|Evidence Inventory_codex", re.I),
    "placeholder text": re.compile(r"\b(?:TODO|TBD|lorem ipsum|placeholder)\b", re.I),
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[tuple[str, str]] = []
        self.h1_count = 0
        self.images_without_alt = 0
        self.html_language = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.html_language = values.get("lang") or ""
        if tag == "h1":
            self.h1_count += 1
        if tag == "img" and not (values.get("alt") or "").strip():
            self.images_without_alt += 1
        for attribute in ("href", "src", "srcset"):
            if values.get(attribute):
                self.references.append((attribute, values[attribute] or ""))


def resolve_reference(page: Path, value: str) -> Path | None:
    candidate = value.split()[0] if "," in value or " " in value else value
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or candidate.startswith(("#", "mailto:", "tel:")):
        return None
    target = (page.parent / unquote(parsed.path)).resolve()
    try:
        target.relative_to(DIST.resolve())
    except ValueError:
        raise ValueError(f"reference leaves public output: {value}") from None
    if target.is_dir() or candidate.endswith("/"):
        target = target / "index.html"
    return target


def main() -> int:
    errors: list[str] = []
    actual_pages = {path.relative_to(DIST).as_posix() for path in DIST.rglob("*.html")}
    if actual_pages != EXPECTED_PAGES:
        errors.append(f"unexpected page set: {sorted(actual_pages ^ EXPECTED_PAGES)}")

    for required in ("assets/headshot.jpeg", "assets/justin-zealand-resume.pdf", "assets/site.css", "assets/site.js"):
        if not (DIST / required).is_file():
            errors.append(f"missing required asset: {required}")
    for required in ("sitemap.xml", "robots.txt"):
        if not (DIST / required).is_file():
            errors.append(f"missing search metadata: {required}")

    for page in sorted(DIST.rglob("*.html")):
        relative = page.relative_to(DIST).as_posix()
        text = page.read_text(encoding="utf-8")
        parser = PageParser()
        parser.feed(text)
        if parser.html_language != "en":
            errors.append(f"{relative}: html language must be en")
        if parser.h1_count != 1:
            errors.append(f"{relative}: expected one h1, found {parser.h1_count}")
        if parser.images_without_alt:
            errors.append(f"{relative}: {parser.images_without_alt} image(s) missing alt text")
        if 'class="skip-link"' not in text:
            errors.append(f"{relative}: missing skip link")
        if 'rel="canonical"' not in text:
            errors.append(f"{relative}: missing canonical URL")
        if "github.com/justinzealand/executive-ai-portfolio" not in text:
            errors.append(f"{relative}: missing primary GitHub evidence link")
        for label, pattern in PRIVATE_PATTERNS.items():
            if match := pattern.search(text):
                errors.append(f"{relative}: {label}: {match.group(0)!r}")
        for attribute, reference in parser.references:
            try:
                target = resolve_reference(page, reference)
            except ValueError as error:
                errors.append(f"{relative}: {attribute}={reference!r}: {error}")
                continue
            if target is not None and not target.is_file():
                errors.append(f"{relative}: broken {attribute}={reference!r}")

    if errors:
        print("\n".join(errors))
        return 1
    print(f"site ok: {len(actual_pages)} pages, assets present, links and public boundary verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
