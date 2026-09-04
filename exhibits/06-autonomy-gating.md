# Exhibit 06 — Autonomy gating (docstring extracts)
These are the module-level design notes of the autonomous merge lane, reproduced verbatim (paths generalized) from four private modules; the code itself stays private. The point of the exhibit is the asymmetry between the attended path (an interactive ship, gated by nothing beyond the reviewer already in the loop) and the unattended path (every conjunct — lease, canonical repo identity, verified sign-off, head-OID binding, independent verification — must hold in order, and any unresolvable answer refuses). Read them as one design: default-deny is the posture, and nothing degrades to allow.

## `ship_merge_wrapper.py`

```text
The SOLE permitted caller of ``gh pr merge`` (build slice B6b;
``acceptance:ship-predicate``, production half of
``acceptance:sole-entrypoint-enforcement``).

Slice B6a (dot-agents PR #205, ``away_gate/enforcement.py``) shipped the
enforcement machinery: the authorization lease, per-operation target
validation, the default-deny side-effect policy, and the pinned entrypoint
inventory. B6a deliberately wired none of it. THIS module is the wiring —
the single wrapper every merge path routes through — plus the ship predicate
that an unattended merge must additionally satisfy.

**Read this before changing anything: the two paths are not symmetric.**

*Attended path (no lease).* An interactive operator session holds no away
grant, so it holds no lease. It invokes ``gh pr merge`` exactly as it does
today — same argv, same runner, same result object handed straight back. The
wrapper adds NO predicate, NO probe, and NO refusal on this path. This is
load-bearing: ``wsroot_helper`` is the workspace's primary ship path and
``autonomous_landing_recovery`` is the Stop-hook lane that rescues unpushed
agent work; a predicate that fired here would break shipping itself. The
value the wrapper delivers on this path is *sole-entrypoint consolidation* —
one chokepoint, pinned by an inventory test — not extra gating.

*Unattended path (a lease is present).* Every conjunct below must hold, in
order, before the merge argv is built. Default-deny throughout: an
unavailable probe, an unresolvable identity, or an unconfigured verifier
REFUSES; nothing degrades to allow.

  1. ``guard_mutating_operation`` (B6a) — lease live, not expired, event head
     unmoved, grant not revoked, ``ship_merge`` class permitted.
  2. Canonical repo identity — the PR resolves inside the repo the caller
     declared, not a fork or a same-named sibling.
  3. Sign-off — ``check_signoff()`` returns ``verified``.
  4. Head binding — the PR's CURRENT head OID equals the sign-off block's
     ``signoff_commit_sha``. This is the post-sign-off-push negative: a push
     that lands after the operator approved moves the head, the equality
     fails, and the merge is refused. It closes exactly the revocation
     condition quoted in ``EXCEPTIONS.md`` ``agent_auto_merge_code_review``:
     *"if check_signoff ever returns verified on a stale signoff_commit_sha
     after a fresh push … revoke and revisit."*
  5. ``/ship`` verification for that SAME head.

Conjunct 5 has no default implementation on purpose. There is no programmatic
``/ship`` verifier today, so ``ship_verifier=None`` refuses with
``REASON_SHIP_VERIFICATION_UNAVAILABLE`` rather than silently skipping a named
acceptance conjunct. An unattended merge therefore cannot complete until B7
activation supplies one — which is the correct state for an inactive gate, and
keeps the unattended path strictly harder to reach than the attended one.

The away-gate import is soft and lease-scoped: with no lease the module is
never imported (no cross-repo dependency on the attended path); with a lease
an import failure REFUSES (``REASON_GUARD_UNAVAILABLE``) rather than
proceeding unguarded — the same partial-ship safety mechanism
``agent_review_gate_pre_merge_check`` uses for its ImportError path.
```

## `agent_dispatch_lane.py`

```text
Agent-dispatch lane — unattended backlog-item implementation (deterministic core).

Plan: <workspace>/delegated-work/docs/plans/agent-dispatch-lane.md

A cron-driven headless coding agent claims up to N eligible ``can-do-batched``
backlog items per run (N bounded, default low) — items tagged ``[auto-impl:ok]``
that ship a valid *acceptance contract* — implements each in an isolated
worktree until its pre-declared failing acceptance test goes green, and
auto-merges the PR only after every pre-merge gate (including a cross-agent
code-review gate) passes.

This module is the DETERMINISTIC CORE. It ships **feature-flagged OFF**: the
operational gate denies dispatch unless an ``enabled`` flag file is present AND
no kill switch is active, so a bare run is a no-op that only writes a heartbeat.
The live model-driving driver, the 3-runtime live-parity harness, and the
scheduling/activation step are separate, operator-signed follow-ons (see the
``## Sunset`` / activation section of ``docs/agent-dispatch-lane.md``).

Architectural home (plan Open question, resolved): this lives in
``<workspace>/scripts/`` — matching the overnight-detect-and-fix precedent
(``overnight_dispatch.py`` et al.) — and drives models only via the
``codex exec`` / ``claude -p`` CLI subprocess, so delegated-work's
"engine does not call models directly" invariant is never diluted.

Substrate reuse (NOT reinvented — plan Frozen decision):
  - fence enforcement                → ``overnight_dispatch._path_in_globs`` / ``_snapshot_status`` / ``_diff_names``
  - registry pre-action preflight    → ``overnight_pre_action_gate._load_registry_snapshots`` (all five registries)
  - cross-agent code-review verdict  → ``langgraph_sidecar.agent_review_gate.gate.review_diff`` (injected seam)
  - selection                        → ``backlog_selector.select_execution_queue`` (filtered to ``[auto-impl:ok]`` + valid contract)
  - claim                            → ``item_preflight`` claim records
```

## `deferred_chip_executor_bridge.py`

```text
Deferred-chip executor bridge — drain the Tier-1/2 queue through the lane gate.

Plan: <workspace>/docs/plans/deferred-chip-executor-bridge.md

`deferred_chip_pipeline.py` DETECTS + CLASSIFIES deferred-chip deposits (Tier
0/1/2) and `overnight_sidecar_wire_in.py` QUEUES Tier 0 to the morning digest.
Nothing IMPLEMENTS Tier 1/2 work — the boundary is documented at
`overnight_sidecar_wire_in.py:275-278`. This module is that missing executor.

It is a thin ``run()``-analog whose *selection* is the deferred-chip classifier
instead of the backlog selector: the four-part implementation gate (deterministic
implement driver -> cross-agent review -> merge -> post-action verify) lives
ENTIRELY inside the injected ``implement_fn`` built by
``agent_dispatch_lane.build_rung3_implement_fn`` / ``build_nightly_implement_fn``.
The bridge REUSES the lane's per-item primitives (``red_at_claim_check``,
``parse_contract``, the idempotency ledger, ``clamp_max_items``); it does NOT
reimplement the gate and never touches the command-template auto-fix path.

**Feature-flagged OFF by default.** Three gates must ALL hold before a chip is
dispatched (any absent => a no-op that writes only a heartbeat + run artifact):
  (a) the bridge's OWN operational gate (``enabled`` flag + kill switch),
  (b) the agent-dispatch lane is armed (``agent_dispatch_lane.operational_gate``),
  (c) the EXCEPTIONS ``deferred_chip_autonomous_implementation`` carve-out present.
Even when all three hold, the DEFAULT ``implement_fn`` provider returns the lane's
placeholder (records needs-operator), so an unproven live merge is impossible
until an ACTIVATION step (separate, operator-signed) injects a real driver.
Building this module activates nothing: no flag is armed, no cron installed, no
sign-off row authored, and the bridge is not wired into the overnight sidecar.
```

## `autonomous_landing_recovery.py`

```text
Autonomous landing recovery for stranded config-repo commits.

Ship 1 lands this module with ``RECOVERY_LANE_ENABLED = False`` so it is dark
code — no consumer invokes the recovery path, and the flag-off short-circuit
in :func:`recover` returns before any git op, ``gh`` call, or audit-log write.

Ship 2 is a 2-line diff that flips the flag to ``True`` and populates the
``EXCEPTIONS.md`` ``agent_auto_merge_code_review`` machine block's
``signoff_pr_number`` + ``signoff_commit_sha`` fields with Ship 1's stable
merged-commit metadata. Ship 3+ (every real recovery PR) then satisfies
``agent_review_gate_pre_merge_check.check_signoff`` against that stable SHA.

See ``docs/plans/claude-config-detached-head-recovery-v2-feature-flag-bootstrap.md``
for the full contract. Failure-mode table lives in the plan's
"Failure recovery contracts" section — each row has a matching classifier
outcome here (see :func:`classify_recovery_state` + refuse-reason constants).
```
