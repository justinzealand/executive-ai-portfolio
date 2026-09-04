# Public-safety inventory (staging, 2026-09-04)

Status: **private staging**, pushed to the PRIVATE repository `justinzealand/executive-ai-portfolio` on 2026-09-04. Nothing here is public. This file is the human-readable record of what every file is, where it came from, what was removed, and what still needs a decision. Delete it before publication or keep it as `docs/PROVENANCE.md` after trimming the decision items.

Gate: `python3 scripts/check_public_safety.py` → **clean, 0 findings** (patterns: absolute home paths, tailnet hostnames, non-loopback IPs, Slack channel ids and names, emails, phone numbers, Notion/artifact URLs, credential-store references, personal-domain product names, session UUIDs, secret shapes). Two literals are hand-allowed: the LinkedIn URL in the README, and a test fixture string containing no real path.

Exhibit tests: `cd exhibits/04-governance-as-code && python3 -m pytest -q` → **394 passed** (one test excluded because it imports a private 3,600-line module; recorded in that exhibit's README).

## Authored for this repository (no source copy)

| File | Notes |
|---|---|
| `README.md` | 248 words before the case-study links, plus one diagram. Numbers: 96 consecutive health days, ~30 repos, kernel 329/98.2%/1.8%/p50 54s, 4,419/240 prompts. |
| `METHOD.md` | Attribution rules, evidence taxonomy, per-metric interpretation table, the claims-not-made list (moved here from the README per review), source discipline. |
| `case-studies/01..03` | Tightened from the drafted case studies (≈20% shorter), same section structure, one diagram each, Evidence footer added. |
| `FIELD-NOTES.md` | 12 lessons paraphrased from the private memory corpus; incident-specific names and ids removed. |
| `diagrams/*.mmd` | The four mermaid diagrams, also embedded inline. |
| `scripts/check_public_safety.py` | The gate. |

## Copied or excerpted from private artifacts

| File | Source (generalized) | Treatment | Redaction classes |
|---|---|---|---|
| `exhibits/01-charter-and-operating-contract.md` | workspace `CHARTER.md` v10i + `AGENT_OPERATING_CONTRACT.md` | Excerpt (problem, success signals, principles, hard constraints, tripwires, non-goals; contract's escalation, do-not-escalate, continuation, anti-bloat) | paths; per-constraint binding annotations; change log; the "single father" clause; personal tool names; channel names |
| `exhibits/02-proportional-governance/policy-2026-08-08.md` | `docs/decisions/…` | Whole | paths |
| `…/drift-decision-2026-07-10.md` | `docs/decisions/…` | Whole | paths |
| `…/drift-investigation-2026-07-18.md` | `docs/investigations/…` | Whole | paths, channel names |
| `…/timeline.md` | Authored from the above + charter change log + kernel README + restoration plan | Authored; every row verified against a source | none needed |
| `exhibits/03-review-kernel/README.md`, `state-model.md` | agent-skills `review-kernel/` | Whole | paths |
| `…/observations.json`, `observations.md` | Verbatim stdout of the kernel's observations module, 2026-09-04 | Whole + authored explainer | none (JSON has no paths) |
| `exhibits/04-governance-as-code/*.py` | workspace `scripts/` | Whole, behavior unchanged; `WORKSPACE_ROOT` made env-overridable in two modules | real tailnet hostname → placeholder (3); operator handle → placeholder (13); `~/projects` → `<workspace>` in comments/docstrings (13); header comment added. `~/projects`-style paths remain in 30 code strings (deny/nudge messages, a protected-path glob registry) because tests assert on them; they reveal a directory layout, not a person. |
| `exhibits/05-overnight-orchestrator.md` | `delegated-work/docs/overnight-detect-and-fix.md` | Whole | paths (36), channel names (5) |
| `exhibits/06-autonomy-gating.md` | Module docstrings of four merge-lane modules | Docstrings only, whole | `~/projects` → `<workspace>` (3) |
| `exhibits/07-observability/deterministic-alert-path.md` | `scripts/health_alert_send.py` docstring + devlog 2026-06-25 | Docstring + authored rationale | channel name; channel/chat id constants not reproduced |
| `…/ops-retrospective-2026-08-28.md` | `docs/ops-retrospectives/…` | Whole | paths; a ticket-price tracker, a finance tool, a school monitor, and one side-project repo → generic phrases |
| `…/postmortem-2026-08-30-gateway-outage.md` | `docs/incidents/…` | Whole | paths; credential-store reference |
| `…/watchdog-design-notes.md` | `openclaw-watchdog/README.md` | Excerpt (pipeline block, detection paths, defaults, design decisions) | path; "Notion triage page" → "triage database" |
| `exhibits/08-operator-attention.md` | `docs/architecture/operator-attention/PROBLEM-BASELINE.md` | Excerpt (Problem, Desired outcomes) + authored Method and findings | id markers, paths, hashes; **one availability-forecast clause generalized** (see decisions); raw corpus deliberately unpublished |
| `exhibits/09-agent-boundaries.md` | `openclaw-config/workspace/SOUL.md` § Boundaries | Whole section | paths; personal tool names |

## Deliberately excluded

Raw prompt corpora and classification datasets; discovery answers; session transcripts; the five registries (allowlist, exceptions, surfaces, always-on, known-chronic); recurring-costs; personal-domain products (continuity, mail triage, email briefs, finance, school, tickets, dashboard, decisions store, wiki content); `delegated-work-host` and `codex-local-supervisor` code (operator bindings); all agent-config repos; the memory directory; the two evidence audits' private coaching notes; the competency matrix (held back from the initial README per review); phone, email, salary, Notion links.

## Decisions settled 2026-09-04 (review round 2)

1. Exhibit 08: generalized wording kept. 2. Exhibit 09: operator-interaction bullet paraphrased (operator is not the routine implementation or PR-review layer). 3. Exhibit 04: generic tilde paths kept. 4. Licensing: MIT for `exhibits/04-governance-as-code/` and `scripts/`, CC BY 4.0 for everything else (`LICENSE.md`, `LICENSE-CODE`, `LICENSE-CONTENT`). 5. Field notes: all 12 kept. 6. Contact: LinkedIn only. Also applied: positive authorship framing in README and Case Study 1; LangGraph removed from the Case Study 1 runtime inventory, Hermes kept as an evaluated runtime.

## Superseded decision list (kept for the record)

1. **Exhibit 08 candor.** The verbatim problem baseline says the operator's attention will become "materially more constrained when a full-time job begins". I generalized it to "when the operator's availability is materially reduced" and recorded the edit in the provenance line. Confirm the generalization, or restore the original, or drop the bullet.
2. **Exhibit 09 wording.** The boundaries say "Justin does not use git or review diffs/PRs/code." It is accurate and consistent with the charter's "operator does not review PRs" design, but a skeptical CTO may read it cold. Recommendation: keep it; the README and METHOD frame it as a design choice.
3. **Exhibit 04 tilde paths.** Thirty `~/projects`-style strings remain in guard code because tests assert on them. Recommendation: keep (generic layout, not identifying); alternative is a global rename that touches tests.
4. **License.** None chosen. Recommendation: CC BY 4.0 for prose and exhibits, MIT for the code in Exhibit 04, stated in a short `LICENSE.md`.
5. **Field notes.** Twelve lessons. Recommendation: keep; cut to eight if the repo reads long.
6. **Contact line.** The README carries only the LinkedIn URL. Recommendation: keep it that way.

## Remaining factual or narrative issues

- Kernel metrics move daily; the README, METHOD, Case Study 3, and `observations.json` were aligned to the 2026-09-04 run (329 / 323 / 6). Re-run the command and re-align on the day of publication.
- "96 consecutive days" is as of 2026-09-04; update or phrase as "over three months" at publication.
- The 2025/2026 chronology line in the README should be mirrored in the resume's independent-period entry so the two surfaces agree.
- The README first screen is 248 words, just under the 250–400 target; it can absorb one more sentence if desired.
