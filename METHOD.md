# Method, attribution, and claim discipline

This document is the rigor behind the README. It says how the work was done, how the evidence was gathered, what each kind of evidence can and cannot support, and which claims I deliberately do not make.

## 1. How the work was done

The environment followed one lifecycle for anything non-trivial: a written **charter** (the immovable layer: problem, success signals, principles in priority order, hard constraints, tripwires) → a **plan** that inherits the charter's constraints and states goals, acceptance criteria, and non-goals → **review** by independent model reviewers under a deterministic kernel → **implementation** by a coding agent in an isolated worktree → **ship** through a pull request with verification → **closeout** that records decisions, learnings, and open items. Exhibit 01 shows the charter and the operating contract; Exhibit 02 shows how the review step was tuned, over-tuned, and corrected.

My role throughout: set the problem and the outcome; choose architecture and decision rights; write or approve the constraints; adjudicate the reviews; decide what may run unattended; read the evidence; change the system when the evidence said so. I did not write implementation code, and the environment was designed so that its operation does not depend on my doing so.

Tools: Claude Code and Codex as engineering agents; OpenClaw as the always-on control plane for scheduling, messaging, approvals, and consequential actions; Hermes as a late-added bounded worker; LangGraph for one delegated-work engine; Ollama-hosted local models for privacy-sensitive lanes. Frontier-model access was subscription-based by policy; no metered model APIs were used.

## 2. Attribution

- **Use:** designed, directed, specified, governed, configured, integrated, operated, tested, evaluated, iterated.
- **"Built"** means built through LLM-directed implementation under constraints I set. It never means hand-written.
- Do not infer authorship from Git metadata. Most commits carry my name because they land through hooks and ship paths I own, and only part of the history carries a trailer naming the agent that wrote the code. The authoritative statement is the platform charter, which requires that implementation be written by AI coding agents under my direction; the metadata understates that.
- Where a document in this repository was itself drafted by an agent from my direction, it says so in its provenance note.

## 3. Evidence taxonomy

Three kinds of experience are kept separate: **led in an organization** (my prior executive roles; not the subject of this repository), **built, configured, and operated personally** (this environment), and **studied or evaluated** (2025 and ongoing reading, courses, and tool evaluations; supports informed opinion, not operating claims).

Maturity labels used for the environment's systems, from strongest to weakest: production-like (private) → repeatedly used workflow → working internal system → prototype → experiment. "Production-like (private)" means persistently running or scheduled, recurrently used, with live or terminal evidence, but single-user and without enterprise operating commitments.

## 4. Measurement method and how to read each number

Every metric in the README or a case study has a source that can be regenerated from the private workspace, and an interpretation that limits it.

| Metric | Source | What it supports | What it does not support |
|---|---|---|---|
| ~6-month window (2026-03-27 →) | earliest attributable session and first commit in the environment's repositories | sustained operation | outcomes or scale; it is a duration, not a result |
| 132 consecutive days of health observations | one dated JSON per day from the deterministic health collector, no gaps | continuity of operation | that the platform was healthy (the verdict was red on most of those days) |
| Review kernel: 600 attempts, 591 content verdicts (98.5%), 9 harness faults (1.5%), p50 56s, p95 114s | the kernel's own packet ledger, regenerated 2026-10-10 (Exhibit 03) | reliability of the review harness against targets I froze in advance | quality of the reviewed plans; enterprise service levels |
| 4,419 operator prompts / 240 manually reviewed samples over 30 days | a reproducible prompt-log analysis with a documented correction (Exhibit 08) | the size and shape of the human coordination burden | productivity improvement; anything about other operators |

Activity counts (commits, pull requests, tests, memory files, lines of code) exist and are large. They are deliberately not published: they measure agent activity, which is exactly the thing this work argues is the wrong measure.

## 5. Claims I do not make

- Personal authorship of the implementation code. It was written by AI coding agents under my direction, not by me and not by another developer.
- Model training, fine-tuning, or machine-learning research.
- Enterprise-scale, multi-tenant, customer-facing, or commercial production.
- Quantified productivity, quality, revenue, or cost improvement from this environment.
- Unconstrained autonomy. The architecture uses approval, no-send, local-only, and fail-closed boundaries on purpose.
- That the platform is uniformly healthy or self-healing. Its own health verdict was red on most recorded days, some remediation automation became a source of failures, and the verdict itself was sometimes wrong in both directions: false reds from stale proxies and at least one false green from a model summarizing a non-green report. Making the signal trustworthy was its own line of work.
- That agents "freely collaborated." The fabric is orchestrated consultation with one retained owner.
- Security certification, external audit, penetration testing, or a service-level agreement.

## 6. Source discipline for this repository

Everything here is an excerpt or a sanitized copy of a private artifact, snapshotted 2026-09-04, with the duration, health-day count, and review-kernel metrics refreshed 2026-10-10. Redaction removed filesystem paths, hostnames, network identifiers, messaging channel identifiers, personal contacts, and the names of personal-domain products; it did not alter reasoning, dates, failure detail, or numbers. Raw prompt corpora and session transcripts are deliberately unpublished. A public-safety check runs against every file before publication.
