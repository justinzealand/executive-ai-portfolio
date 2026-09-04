_Provenance: original location `<workspace>/docs/decisions/2026-07-10-plan-review-consultation-drift.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized._
_Content otherwise unchanged._

# Decision packet — plan-review & registries-consultation drift (2026-07-10)

## Resolution (2026-07-10)

**Operator went with the default: Option 4 (split acute vs chronic).** Option 2 (auto-inject at scaffold time) is deferred to a follow-on — not shipping in the same change. Implementation lands on/after **2026-07-17** (day 8), giving the daily surface a week to age the growth signal before we cut over. Tracked as a `disp:wait` P2 entry in `ROADMAP.md` under "Plan-review + registries-consultation drift — Pattern XVI (2026-07-10)".

*Original packet below preserved for context.*

---

## Decision needed
The plan-review and registries-consultation drift checks are BOTH growing faster than the workspace retrofits them: `repo.plan_review_drift` went 2 → 29 unreviewed plans over 12 days (+27), and `repo.plan_registries_consultation_drift` went 23 → 39 missing sections over 5 days (+16). Every daily-health run yellows on both. Their nag rate exceeds the operator's willingness to act, so the signal is degrading into noise. What is the right disposition?

## Context
- The plan-review skill takes 5–15 minutes per plan; catching up 29 unreviewed + 7 invalid-header plans is ≈4–8 hours of focused work, and new plans land every day.
- Both detectors nag at three points already (plan-write Stop nudge, `/ship` warn, daily-health). Yet drift keeps growing. That evidence says the friction is asymmetric: nagging catches *this* plan; catching up on the tail requires deliberate batch time.
- Both classes are "detector shipped, remediation catch-up rate < ship rate" (Pattern XVI in the recovery report). This is a structural class, not a per-item bug.

## Options
1. **Batch retrofit + freeze** — spend one dedicated session (or one workflow) walking the 29 unreviewed plans, running `/plan-review` on each and adding `## Registries consulted` where missing. Then enforce the pre-ship gate (block ship on unreviewed / missing-section). — *Consequence:* one-time ≈8h operator/agent cost, zero recurring nag. Cost front-loaded; the gate then holds the invariant.
2. **Auto-inject at scaffold time** — extend `/plan-init` to write an empty `## Registries consulted` section by default; extend the plan-review-detector to treat "empty registries block" as reviewed=false but not-yet-invalid. Combine with (1) for the tail. — *Consequence:* fix-forward on new plans, tail remains. New plans stop contributing to `plan_registries_consultation_drift` count; existing 39 stay warning until retrofit.
3. **Demote both to `digest_only`** — accept that the nag rate has outpaced remediation and relegate both checks to the weekly developer digest rather than the daily verdict. — *Consequence:* daily surface calms; loss of daily accountability. Invariant becomes advisory-only. Weekly `/ops-retrospective` still catches acute regressions.
4. **Split acute vs chronic** — add per-plan age: WARN only on plans younger than N days (e.g. 7) that landed unreviewed. Older plans move to a separate `stale_plans_backlog` count. — *Consequence:* daily surface names ONLY the new drift (the actionable class); the backlog stays visible but doesn't nag. Same Pattern-XVI-response as `stale_git_locks` / `orphan_observations` chronic-vs-fresh split.

## Recommendation
**Option 4** (split acute vs chronic), combined with a light form of Option 2 (auto-inject empty section at plan-init).

Rationale: Options 1 and 3 are extremes. Option 1 buys a one-time cleanup but the class recurs. Option 3 abandons the invariant on the daily surface — this is what M5 tripwires against. Option 4 preserves the invariant on new work (where the marginal cost of adherence is minutes) and reframes the tail as a backlog the operator can burn down when calendar permits. Option 2 is a small wrapper on top that removes the friction of "remember to add the header" from every plan-init interaction.

## Default if left unanswered
For 7 days: no change; both checks continue to yellow. On day 8, apply Option 4 in code (split acute/chronic in `plan_review_detector.classify_repo` and `plan_registries_consultation_detector.classify`; register `plan_review_chronic_backlog` + `plan_registries_consultation_chronic_backlog` as `digest_only` and keep the acute variants on the daily surface). No Option 2 unless explicitly authorized.

## Consequence of default
Daily surface stays yellow-on-drift for the full 7-day window (operator continues to see the signal). Then acute/chronic split lands and the daily surface calms unless a new plan lands unreviewed within N days — which is the invariant we actually care about.

## Related
- `<workspace>/scripts/plan_review_detector.py`
- `<workspace>/scripts/registries_consultation_detector.py`
- `<workspace>/scripts/daily-health-check.py` (`collect_plan_review_drift`, `collect_plan_registries_consultation_drift`)
- `<workspace>/CHARTER.md` — M5 tripwire: net-additional surface / net-additional daily noise auto-routes.
- `feedback_pair_detective_guard_with_structural_invariant.md`
- `feedback_check_needs_age_or_streak_escalation.md`
- `feedback_stale_acked_rows_have_no_exit_path.md`
