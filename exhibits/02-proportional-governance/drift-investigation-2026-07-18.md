_Provenance: original location `<workspace>/docs/investigations/2026-07-18-plan-review-drift-decision.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized; Slack channel name generalized._
_Content otherwise unchanged._

# Plan-review + registries drift — decision packet (2026-07-18)

## TL;DR

Three coupled counts are monotonically growing:

| Signal | Today | 14d ago | Delta | Repos |
|---|---|---|---|---|
| Unreviewed plans (`repo.plan_review_drift`) | 46 | 20 | +130% | 5 (projects=39, delegated-work=4, delegated-work-host=1, lexicon-ios=1, openclaw-watchdog=1) |
| Invalid Guardrail Header | 17 | 4 | +325% | 2 (projects=16, openclaw-watchdog=1) |
| Missing `## Registries consulted` | 67 | 23 | +191% | 4 (projects=60, delegated-work=5, delegated-work-host=1, openclaw-watchdog=1; lexicon-ios=0 — clean) |
| Repos with plans but no CHARTER.md | 1 | 0 | new | openclaw-watchdog |

Root cause is upstream, not the queue: **`<workspace>/scripts/templates/PLAN.md` does not contain a `## Registries consulted` section**, so every plan scaffolded via `/plan-init` starts non-compliant. The skill body (CC + Codex parity) tells the agent to hand-inject the section as Phase 3.5, but delegated to prose it is skipped in the majority of sessions. Growth math: **61 NEW plans committed in `<workspace>/docs/plans/` in the past 14 days** (~4.3/day); review cadence covered ~35 (~2.5/day). Net drift: +26/2wk, matching the observed delta.

Second-order finding: the `repo.plan_review_drift` health check counts `n_unreviewed` and `n_invalid` over ALL plans, INCLUDING plans marked `plan_lifecycle: implemented | superseded | retired`. The detector already computes a `reviewable_lifecycle` bool but the aggregator does not filter on it. ~5 plans (~11% of the reported 46) are terminal-lifecycle noise.

Third-order finding: `find_review_packet` matches by exact SHA-256 hash of the plan text. Any post-review edit (which is the normal iteration flow) permanently unlinks the plan from its packet. Real unreviewed count is likely lower than 46; some are "reviewed then refined." I did NOT quantify this — flagging as an open follow-up.

**Recommended: Option E-hybrid** — ship Option C (template auto-injection + detector lifecycle-aware) TODAY as the shift-left fix, then run Option A (batch review) at reduced scope on top-K plans the operator selects. Option D (mark shipped) removes ~5 items mechanically. This stops the generator, cleans the tail, and preserves review discipline where it matters.

---

## Phase 1 — Enumeration + bucketing

### Per-repo authoritative counts (from `<openclaw-config>/health/daily-health-check-2026-07-18.json`)

| Repo | n_plans | n_unreviewed | n_invalid | n_missing_registries | Charter? |
|---|---|---|---|---|---|
| projects | 76 | 39 | 16 | 60 | ✓ |
| delegated-work | 7 | 4 | 0 | 5 | ✓ |
| delegated-work-host | 1 | 1 | 0 | 1 | ✓ |
| lexicon-ios | 2 | 1 | 0 | 0 | ✓ |
| openclaw-watchdog | 1 | 1 | 1 | 1 | **✗** |
| **totals** | **87** | **46** | **17** | **67** | |

### Bucketing (all repos)

- **Recent + high-value / needs review** (edited <7d, `draft`|`active` lifecycle, valid header, cited by open work): the natural review-queue backlog. ~15-20 plans by inspection, but the SHA-hash matching problem means I cannot mechanically produce this list — reviewed-then-edited plans look identical to never-reviewed. Human triage required to sort.
- **Terminal lifecycle (implemented / superseded / retired) — should not count as unreviewed** (5 plans):
  - `architecture-first-class-platform-process-phase1b-pilot.md` (implemented)
  - `cross-agent-review-concurrency-mitigation.md` (implemented)
  - `claude-config-detached-head-recovery.md` (superseded)
  - `plan-review-open-issues-roadmap.md` (retired)
  - `pulse-stale-suppression-sweep.md` (implemented, openclaw-watchdog)
  These are already correctly-labelled — the detector marks them `reviewable_lifecycle: false`, but the aggregator ignores the field.
- **Non-plan artifacts sitting under `docs/plans/`** (5 files, all in projects):
  - `agent-session-halt-phone-alerts-problem-baseline.md`
  - `condition-fingerprint-schema-audit.md`
  - `health-alert-review-closure-2026-07-13-operator-evidence.md`
  - `july14-health-stabilization-problem-baseline.md`
  - `session-progress-current-artifact-audit.md`
  These have `plan_lifecycle: (none)` and are missing the review-header entirely. They are PROBLEM-BASELINE / evidence / audit companion docs — a distinct artifact class the detector treats as a broken plan. This is a schema gap: `docs/plans/` is being used as a scratch dir for non-plan artifacts because there is no distinct home.
- **Draft plans with "superseding language in prose" errors** (6 plans, all projects): the invalid-header check flags any `draft` plan whose prose contains words like "supersede" / "replaces" even when referring to prior versions or related plans by NAME, not declaring itself superseded. False positives:
  - `agent-config-landing-path-coverage.md`, `architecture-first-class-platform-process.md`, `multi-phase-operator-execution.md`, `overnight-pre-action-gate.md`, `problem-first-review-and-explore-to-commit.md`, `problem-first-review-and-explore-to-commit-stage-b.md` (inspection suggests they mention "supersedes" in a meta context, not as their own state).
- **Legit missing Goals/Acceptance sections** (2 plans): `oc-cron-family-a-fallback-canary.md`, `pulse-incident-lifecycle-producer-contract-2026-07-13.md`. These are real drafting gaps.
- **Missing `## Registries consulted` — the systemic gap** (67 plans, 4 repos): every single non-lexicon-ios plan in the workspace is missing this section. lexicon-ios is 0/2 which strongly implies lexicon uses a different template flow that DOES scaffold the section.

Full per-plan enumeration (77 projects + 7 delegated-work + 1 delegated-work-host + 1 openclaw-watchdog) available via `python3 <workspace>/scripts/plan_review_detector.py --json` from each repo root.

---

## Phase 2 — Growth-rate analysis

### Authoring vs reviewing cadence

- **New plans committed to `<workspace>/docs/plans/` past 14d:** 61 (git `--diff-filter=A`)
- **Plans touched (added or modified) past 14d in projects:** 63 filesystem mtime, 63 git-committed
- **Review packets created past 14d:** 1,368 in `<agent-skills>/state/review-convergence/` (BUT these are per-pass, not per-plan; a single plan review commonly spins up 2-4 packets)
- **Estimated review throughput:** ~35 plans reviewed to completion in the same window (derived from 46 unreviewed = 87 total − 41 reviewed, minus the reviewed-then-edited unlinkage factor).

**Bottom line: authoring ~4.3/day, reviewing ~2.5/day. Net drift +1.8/day, tracking with observed +26/2wk delta on unreviewed and +44/2wk on missing-registries.**

### Are sessions using `/plan-init` at all?

Reading the plan corpus:
- Most plans have valid Guardrail Headers (`Review intent`, `Supersedes`, `Inheritance audit`, `Scope check`, `Plan lifecycle`) — evidence they went through `/plan-init` or a hand-copy of its output.
- The `## Registries consulted` section is uniformly absent from every non-lexicon plan.

That combination points unambiguously at the template as the root cause: `/plan-init` scaffolds from `<workspace>/scripts/templates/PLAN.md`, which does NOT include the section. The skill body (`<claude-config>/commands/plan-init.md` Phase 3.5; `<agent-skills>/skills/plan-init/SKILL.md` Step 3a) DOES instruct the agent to add the section — but delegating a whole-section injection to prose skill instructions is exactly the drift-generator pattern documented in `feedback_lens_in_skill_body_not_in_prompt.md`.

**Confirmed:** the template is the shared source of the invalid+missing pattern. Fixing it forward doesn't fix the 67 back-catalog, but stops daily drift immediately.

### SHA-hash unlinkage inflates the review-drift signal

`find_review_packet` (plan_review_detector.py:564-590) matches a plan to its packet by exact SHA-256 of stripped text. Any post-review edit (typo fix, scope-refinement, adding a section based on review feedback) permanently unlinks the plan from its packet. **This is architecturally guaranteed to inflate the drift metric** because a normal review flow is: draft → /plan-review → revise per findings → ship. Step 3 unlinks step 2.

I did not compute the inflation factor — quantifying it needs a fuzzy-hash / edit-distance / packet-name-match probe, out of scope for this packet. Filing as prevention follow-on P1.

---

## Phase 3 — Options

Costs are rough estimates in agent-minutes (a "review" here is one `/plan-review` pass; a "sweep" bundles many).

### Option A — Batch-fix (mass /plan-review on top-K plans)

**What:** Operator picks top-K plans from the "recent + high-value" bucket. Spawn K parallel `worker_launcher.py` review sessions (Codex + OpenClaw consults). Ships review packets against current plan hashes.

**Cost:** ~15-25 min per plan × K. At K=20, one afternoon (5-8h wall-clock with parallelism cap ~4).

**Clears:** the K plans that got reviewed. Cost per plan is real (each spawns 3 model turns + convergence).

**Doesn't clear:** the 67-missing-registries count (a review pass on a plan without the section doesn't ADD the section — the skill body Phase 3.5 does, and only in interactive `/plan-init`). It also doesn't stop tomorrow's drift generator — new plans keep landing non-compliant.

**Tomorrow's counts:** unreviewed drops by K, then re-inflates by ~5/day. Missing-registries unchanged. Invalid unchanged.

**Verdict:** treats symptoms, expensive per unit, and the hash-unlinkage issue means each re-edit un-does it. Not recommended standalone.

### Option B — Right-scope (loosen the requirement)

**What:** Add a `plan_scope: internal | shared | cross-workspace` header field. `internal` (implementation notes, personal design scratch, quick investigations) exempted from `repo.plan_review_drift`. `shared` / `cross-workspace` retains today's requirement.

**Cost:** 1-2h to add field to template, update detector to filter, backfill headers on ~15-20 existing "internal-looking" plans. Prose-defining "shared" vs "internal" is the hard bit — likely a `/plan-review` cycle itself.

**Clears:** structural — probably shaves 40-50% off the effective unreviewed count. Right-sizes discipline to where cross-agent review actually delivers value.

**Doesn't clear:** the missing-registries generator (still a template bug for shared plans).

**Tomorrow's counts:** unreviewed ~20-25 sustainable; drift generator continues on the shared subset.

**Verdict:** correct medium-term posture but a design surface all its own. Should not block the tactical fixes below.

### Option C — Auto-injector (fix the generator)

**What:** Ship three deterministic upgrades in one PR:
1. Add `## Registries consulted` section to `<workspace>/scripts/templates/PLAN.md` with an `n/a — no registered surface touched` default placeholder and a comment reminding the agent to fill in any triggered registries.
2. Update `<claude-config>/commands/plan-init.md` + `<agent-skills>/skills/plan-init/SKILL.md` Phase 3.5 to reference the template scaffold and shift the responsibility from "inject the section" to "fill in the section" (skill-body → template shift-left).
3. Patch `daily-health-check.py::collect_plan_review_drift` (line 20845+) to filter `n_unreviewed` and `n_invalid` on `reviewable_lifecycle` (already computed by the detector; the aggregator ignores it — 3-line change).

**Cost:** ~1.5h. Small, well-scoped, testable. Add prose-invariant tests to `test_plan_init_skill_invariants.py` (already exists) pinning the template contains the section.

**Clears:** the ~5 terminal-lifecycle-mis-counted plans (from `n_unreviewed`), immediately. Stops the daily missing-registries generator: from ship-day forward, every new plan starts with the section stub. Also removes 5 items from `n_invalid` (the 5 non-plan-artifacts get an easier gap-close: either lifecycle=`draft` header added, or moved to a sibling dir).

**Doesn't clear:** the 67-plan back-catalog (existing plans without the section). Doesn't itself run reviews.

**Tomorrow's counts:** unreviewed drops by 5 (lifecycle filter). Missing-registries flat today; declines slowly as plans get closed/archived. Invalid drops by 3-5. **Net-daily drift on missing-registries goes to zero.**

**Verdict:** **highest ROI single ship.** Kills the generator, cleans the terminal noise, unblocks future signal-to-noise. Must ship first regardless of what else is chosen.

### Option D — Archive (mark old plans by disposition)

**What:** For each plan the operator identifies as shipped/abandoned, set `Plan lifecycle: implemented | retired`. Requires operator judgment per plan (Codex/CC can propose based on ROADMAP.md and git history but not decide). Combined with Option C's lifecycle filter, this deletes them from the drift signals.

**Cost:** ~10-20 min operator triage per plan; ~5-10 min agent-assisted classification per plan. At the ~5 obvious terminal-lifecycle plans, and ~15 old (>14d) drafts that read as "shipped or forgotten," ~4h total across a mix of operator + agent time.

**Clears:** ~20 items from the unreviewed count. Structurally right — those plans genuinely aren't awaiting review, and the current signal is a false-positive for them.

**Doesn't clear:** the drift generator. New plans continue accumulating.

**Tomorrow's counts:** unreviewed drops by ~20. Missing-registries unchanged (archived plans still don't have the section).

**Verdict:** natural pairing with Option C. Without Option C, marking plans doesn't help the health signal (the aggregator ignores lifecycle today).

### Option E — Combined (recommended)

**Ship Option C today** (small, deterministic, kills the generator; pre-requisite for D to matter). **Follow with Option D on a batch of ~15 obvious-terminal plans** (operator picks; agent proposes based on ROADMAP + git evidence per plan). **Skip Option A for now** — no benefit until Option C stops the treadmill and the hash-unlinkage issue is understood. **Defer Option B** — pursue it after the tactical fixes when we can see the true "shared plan" backlog volume.

Projected 3-day trajectory under Option E:
- Today (baseline): 46 unreviewed / 17 invalid / 67 missing-registries
- After C ships: 41 unreviewed / 12-14 invalid / 67 missing (no reduction — existing plans need touch)
- After D executes on ~15 archives: 26 unreviewed / 12 invalid / 52 missing
- After 7 more days of drift, WITH C in place: 26 unreviewed (net ±0 assuming review cadence matches authoring) / 12 invalid / 52 missing (net ±0)

Steady-state depends on whether review cadence catches up; but crucially **missing-registries stops growing** on day 1.

---

## Phase 4 — Prevention (regardless of option chosen)

These follow-ons address the underlying drift generators. They should be filed as chips/ROADMAP items even if the operator chooses a different option today.

### P0 — Template auto-injection (contained in Option C)

Ship `<workspace>/scripts/templates/PLAN.md` with `## Registries consulted` scaffold. Add prose-invariant test in `scripts/test_plan_init_skill_invariants.py` pinning presence. Est. 30min.

### P0 — Detector lifecycle-aware (contained in Option C)

Patch `daily-health-check.py::collect_plan_review_drift` to filter `n_unreviewed` and `n_invalid` on `reviewable_lifecycle: true` (field already computed by `plan_review_detector.py`). Add test case pinning the filter. Est. 30min.

### P1 — Quantify SHA-hash unlinkage inflation

Add a diagnostic mode to `plan_review_detector.py` that reports: for each unreviewed plan, does a review packet exist whose plan filename or slug matches, even if content hash doesn't? Output a per-plan "reviewed-then-edited" hypothesis. Choose: relax matching (fuzzy hash / slug match) or keep strict and accept the noise. Est. 3h investigation, 2h implementation.

### P1 — Non-plan-artifact schema gap

`docs/plans/` is being used as scratch for problem-baseline / evidence / audit artifacts. Options: (a) new sibling dir `docs/plan-artifacts/`; (b) detector allowlist for `*-problem-baseline.md` / `*-operator-evidence.md` / `*-audit.md` slug patterns; (c) new lifecycle value `plan_lifecycle: companion` for these. Est. 2h.

### P1 — Growth-rate alert

Add a daily-health WARN when `unreviewed_count` grows by >5 in 7d WITHOUT any reviewed transitions. Signals "review lane starved" as a separate incident class from "unreviewed count > threshold." Est. 2h.

### P2 — Ship Option B thoughtfully

Design `plan_scope: internal | shared | cross-workspace` field per the Option B analysis above. Route through `/plan-review` itself. Est. 1-2 day design + ship.

### P2 — `openclaw-watchdog` needs `CHARTER.md`

Only repo in the workspace with plans but no charter. Either author one (Est. 2h with `/charter`) or move the single plan to a repo that has one.

---

## Anti-recommendations (things NOT to do)

- **Do NOT mass-run `/plan-review` on the 46-plan backlog.** Wall-clock ~10-15h of model turns, doesn't fix the generator, and the hash-unlinkage issue means the moment operator/agent iterates on the reviewed plan the drift signal re-fires. Wait for Option C first.
- **Do NOT delete or `mv` the 5 non-plan-artifacts out of `docs/plans/` without an operator-approved schema decision.** The current locations may be load-bearing for citations in other plans.
- **Do NOT lower any alert threshold to hide the drift signals.** They ARE catching a real problem; muting the signal loses the observability.
- **Do NOT auto-batch a "mark all old drafts as `retired`" sweep.** Some old plans are dormant P2/P3 work legitimately awaiting a review lane; classifying them as retired discards real backlog.

---

## Open assumptions (verified or explicitly transferred)

- Assumption: `<workspace>/scripts/templates/PLAN.md` is the sole template used by `/plan-init`. **Verified** — the two skill bodies both reference the same template path; grep hit only that file for `templates/PLAN.md`.
- Assumption: `find_review_packet` SHA-hash matching is exact — no fuzzy tolerance. **Verified** by reading `plan_review_detector.py:564-590`.
- Assumption: the aggregator does not filter on `reviewable_lifecycle`. **Verified** — `collect_plan_review_drift` in `daily-health-check.py:20879-20888` sums `n_unreviewed` from `assess_repo` output, which itself does not filter on lifecycle (line 676: `sum(1 for p in plans if p.get('readable') and not p.get('reviewed'))`).
- Assumption: growth rate ~4.3/day authoring is workspace-wide. **Partially verified** — 61 new plans in projects repo alone in 14d confirms authoring dominates in projects. Sub-repo authoring rates not measured; likely ≤1/day given only 9 sub-repo plans total.
- Unverified assumption: the hash-unlinkage inflation factor. **Explicitly transferred to prevention follow-on P1** — needs a diagnostic probe to quantify; not blocking today's decision.

## Acceptance-test evidence

- Packet lands at `<workspace>/docs/investigations/2026-07-18-plan-review-drift-decision.md` ✓ (this file)
- Contains per-repo enumeration ✓ (Phase 1 table + bucketing)
- Contains growth-rate analysis ✓ (Phase 2)
- Contains options table with cost/clears/tomorrow ✓ (Phase 3)
- Contains recommendation with reasoning ✓ (TL;DR + Option E)
- Contains prevention follow-ons ✓ (Phase 4)
- the Slack alerts channel one-line post: pending post-ship (this session)

## Registries consulted

- `SURFACES.md` — no new alert channel / dashboard / daily surface added. The one-line Slack post at an alerts channel uses an existing surface. n/a.
- `ALLOWLIST.md` — no new outbound integration. n/a.
- `EXCEPTIONS.md` — no default-deny carve-out invoked. n/a.
- `ALWAYS_ON.md` — no new launchd/cron/daemon proposed. n/a.

n/a — this is an investigation-only artifact, no registered surface touched.
