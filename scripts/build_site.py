#!/usr/bin/env python3
"""Build the public static portfolio with no third-party dependencies."""

from __future__ import annotations

import argparse
import html
import shutil
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "site-src"
OUTPUT = ROOT / "dist"
CANONICAL_BASE = "https://justinzealand.github.io/executive-ai-portfolio/"

PAGES = {
    "index.html": {
        "source": "index.html",
        "title": "Justin Zealand | Technology Executive and AI Transformation Leader",
        "description": (
            "Executive portfolio for Justin Zealand, a technology leader with more than "
            "20 years of experience scaling product and engineering organizations and leading AI transformation."
        ),
        "root": "",
        "nav": "overview",
    },
    "experience/index.html": {
        "source": "experience.html",
        "title": "Executive Experience | Justin Zealand",
        "description": "Justin Zealand's executive record building organizations, modernizing platforms, and connecting technology to business outcomes.",
        "root": "../",
        "nav": "experience",
    },
    "case-studies/index.html": {
        "source": "case-studies.html",
        "title": "AI Leadership Case Studies | Justin Zealand",
        "description": "Three evidence-led case studies about AI-native engineering, proportional autonomy, verification, and accountable leadership.",
        "root": "../",
        "nav": "case-studies",
    },
    "case-studies/operating-model/index.html": {
        "source": "case-operating-model.html",
        "title": "Designing an AI-Native Engineering Operating Model | Justin Zealand",
        "description": "A case study about shifting AI engineering from session supervision toward successful objectives with less human coordination.",
        "root": "../../",
        "nav": "case-studies",
    },
    "case-studies/proportional-governance/index.html": {
        "source": "case-proportional-governance.html",
        "title": "Governing Autonomous Work Without Eliminating Autonomy | Justin Zealand",
        "description": "A case study about allocating AI autonomy by consequence, enforcing critical boundaries, and measuring the cost of governance.",
        "root": "../../",
        "nav": "case-studies",
    },
    "case-studies/trustworthy-ai-work/index.html": {
        "source": "case-trustworthy-ai-work.html",
        "title": "Making AI-Generated Work Trustworthy | Justin Zealand",
        "description": "A case study about independent AI review, deterministic controls, measurable reliability, and accountable human ownership.",
        "root": "../../",
        "nav": "case-studies",
    },
    "leadership/index.html": {
        "source": "leadership.html",
        "title": "Leadership and AI Point of View | Justin Zealand",
        "description": "Justin Zealand's leadership philosophy and point of view on AI-enabled technology operating models.",
        "root": "../",
        "nav": "leadership",
    },
    "resume/index.html": {
        "source": "resume.html",
        "title": "Résumé | Justin Zealand",
        "description": "Download Justin Zealand's executive résumé and review his experience across technology, product, operations, and AI transformation.",
        "root": "../",
        "nav": "resume",
    },
    "contact/index.html": {
        "source": "contact.html",
        "title": "Contact | Justin Zealand",
        "description": "Contact Justin Zealand about executive technology, product, engineering, and AI transformation leadership opportunities.",
        "root": "../",
        "nav": "contact",
    },
}


def render_page(config: dict[str, str]) -> str:
    root = config["root"]
    body = (SOURCE / "pages" / config["source"]).read_text(encoding="utf-8")
    body = body.replace("{{ROOT}}", root)
    template = (SOURCE / "layout.html").read_text(encoding="utf-8")
    replacements = {
        "{{TITLE}}": html.escape(config["title"]),
        "{{DESCRIPTION}}": html.escape(config["description"], quote=True),
        "{{CANONICAL}}": html.escape(config["canonical"], quote=True),
        "{{ROOT}}": root,
        "{{BODY}}": body,
        "{{NAV_OVERVIEW}}": ' aria-current="page"' if config["nav"] == "overview" else "",
        "{{NAV_EXPERIENCE}}": ' aria-current="page"' if config["nav"] == "experience" else "",
        "{{NAV_CASE_STUDIES}}": ' aria-current="page"' if config["nav"] == "case-studies" else "",
        "{{NAV_LEADERSHIP}}": ' aria-current="page"' if config["nav"] == "leadership" else "",
        "{{NAV_RESUME}}": ' aria-current="page"' if config["nav"] == "resume" else "",
        "{{NAV_CONTACT}}": ' aria-current="page"' if config["nav"] == "contact" else "",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def build(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for output_name, config in PAGES.items():
        route = "" if output_name == "index.html" else output_name.removesuffix("index.html")
        config = {**config, "canonical": CANONICAL_BASE + route}
        output = destination / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_page(config), encoding="utf-8")

    asset_output = destination / "assets"
    asset_output.mkdir(parents=True, exist_ok=True)
    for asset in (SOURCE / "assets").iterdir():
        if asset.is_file():
            shutil.copy2(asset, asset_output / asset.name)

    diagram_output = asset_output / "diagrams"
    diagram_output.mkdir(parents=True, exist_ok=True)
    for diagram in (ROOT / "diagrams").glob("*.svg"):
        shutil.copy2(diagram, diagram_output / diagram.name)

    routes = ["" if name == "index.html" else name.removesuffix("index.html") for name in PAGES]
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sitemap.extend(f"  <url><loc>{escape(CANONICAL_BASE + route)}</loc></url>" for route in routes)
    sitemap.append("</urlset>")
    (destination / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")
    (destination / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {CANONICAL_BASE}sitemap.xml\n", encoding="utf-8")


def files_match(left: Path, right: Path) -> bool:
    left_files = sorted(path.relative_to(left) for path in left.rglob("*") if path.is_file())
    right_files = sorted(path.relative_to(right) for path in right.rglob("*") if path.is_file())
    if left_files != right_files:
        return False
    return all((left / path).read_bytes() == (right / path).read_bytes() for path in left_files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify committed output matches source")
    args = parser.parse_args()

    if args.check:
        with tempfile.TemporaryDirectory(prefix="portfolio-build-") as temp:
            candidate = Path(temp) / "dist"
            build(candidate)
            if not OUTPUT.exists() or not files_match(candidate, OUTPUT):
                print("dist is out of date; run scripts/build_site.py")
                return 1
        print("site output is current")
        return 0

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    build(OUTPUT)
    print(f"built {len(PAGES)} page(s) in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
