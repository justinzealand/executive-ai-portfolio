_Provenance: original location `<agent-skills>/skills/review-kernel/docs/state-model.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized._
_Content otherwise unchanged._

# Plan-Review v2 Kernel — Formal State Model

Deliverable: `acceptance:state-model` of `<agent-skills>/docs/plans/plan-review-v2-kernel.md`
(Phase 1, ships BEFORE kernel code). Status: reviewable artifact.

**Executable authority.** The declarative transition table in
`review_kernel/transitions.py` (private; not included in this exhibit) is the single
source of truth; this document is its narrative. Drift between the two is
mechanically bounded: `tests/test_state_model_doc_parity.py` fails when any
dimension value, event type, transition row name, guard, effect, or invariant
id in the code is absent from this document, and the rendered transition table
below is asserted cell-for-cell against the live table.

---

## 1. Aggregate and identity

The aggregate is one **review packet**: a content-addressed review identity
frozen over exact plan bytes (`plan_hash`) plus a machine-generated scope
manifest (`manifest_hash`), an immutable item-identity registry, a consequence
tier, an engine pin, and lineage. A packet comes into existence ONLY through
the `packet_frozen` genesis event and is never mutated in place: **state =
fold(events)** over the packet's append-only event log. Envelope-level
linearization (monotonic sequence, previous-head checksum, expected-head CAS,
idempotency keys) is the event-log layer's contract (Phase 1 kernel core,
frozen decision "Event-log authority + linearization"); the state model treats
those fields as opaque and consumes decoded events.

Within a packet, work happens in numbered **pass attempts**. An attempt is
admitted (`pass_attempt_started` precedes all transport work — goal-telemetry),
runs its reviewer legs, and terminates in exactly one of three outcomes
(goal-three-axis-outcomes): `content_verdict`, `operator_gate`,
`harness_fault`. Plain re-run — a fresh attempt — is always lawful from any
terminal phase.

## 2. State vector — every dimension

The aggregate state is an **orthogonal state vector** (not a single enum).
Scalar dimensions carry one value from a closed domain; structured dimensions
carry typed records. Complete list (field names match
`review_kernel/state_model.py::PacketState`):

| # | Dimension (plan wording) | Field(s) | Domain / shape |
|---|--------------------------|----------|----------------|
| D1 | Packet lifecycle | `packet_lifecycle` | `absent`, `active`, `superseded` (+ `packet_id`, `snapshot_id`) |
| D2 | Pass lifecycle | `attempt_phase`, `attempt_number`, `verdict_token`, `fault`, `attempt_history` | `idle`, `admitted`, `in_review`, `terminal_content_verdict`, `terminal_operator_gate`, `terminal_harness_fault`; monotone attempt counter; one `AttemptRecord` per terminated attempt |
| D3/D4 | Two reviewer-leg lifecycles | `legs["leg_a"]`, `legs["leg_b"]` | per-leg `phase` ∈ `unassigned`, `pending`, `in_flight`, `invalid`, `repair_in_flight`, `usable`, `degraded`, `timed_out`, `failed`; `provider`; per-leg fault record |
| D5 | Retry / repair | `LegState.repair_count`, `LegState.retry_count` | bounded by `REPAIR_BUDGET` = 1 (one bounded repair-reprompt, goal-typed-reviewer-io) and `RETRY_BUDGET` = 2; reset each attempt |
| D6 | Tier + authorization eligibility | `tier`, `authorization_eligibility` | `tier_l`, `tier_s`; `eligible`, `non_authorizing` (a malformed/missing header freezes as Tier L stamped non-authorizing — frozen decision) |
| D7 | Operator gate | `gates` | map gate_id → `GateState(gate_type ∈ {descope_signoff, accepted_risk, disagreement, settlement}, item_ids, status ∈ {open, satisfied})` |
| D8 | Settlement | `settlements` | map item_id → `SettlementState(status ∈ {settled, reopened}, text_hash, classification)` |
| D9 | Disagreement | `disagreement` | `none`, `open`, `operator_decided` |
| D10 | Snapshot / escalation lineage | `lineage_role`, `predecessor_packet_id`, `successor_packet_id` | `root`, `escalation_successor`; a Tier-S successor supersedes its Tier-L predecessor |
| D11 | Provenance / pin | `engine_pin`, `pin_status`, `plan_hash`, `manifest_hash`, `plan_binding` | `pin_status` ∈ `approved`, `unapproved`; `plan_binding` ∈ `bound_current`, `stale` |
| D12 | Authorization / revocation | `authorization`, `authorization_binding` | `none`, `granted`, `revoked`; binding records plan hash, manifest hash, event head, state fingerprint |
| D13 | Item-identity registry | `item_registry` | immutable map item_id → record, stamped at freeze (goal-scope-fidelity; inline markers alone are never trusted) |
| — | Log integrity (fold output) | `FoldResult.integrity` | `intact`, `corrupt_tail` — not event-mutated; a corrupt/truncated tail halts the fold and denies authorization as a retryable harness fault |

Quorum is a **derived predicate**, not stored state:
`quorum_satisfied(tier, legs)` — Tier S requires both legs `usable`; Tier L
requires one `usable` leg (goal-tiering). Tier L freezes `leg_b` as
`unassigned` at attempt start (one primary reviewer first, second as fallback —
acceptance:admission).

`state_fingerprint(state)` is the deterministic content hash of the vector;
the authorization binding records it as the state version evaluated.

## 3. Event alphabet

Declared in `review_kernel/events.py::EVENT_TYPES` (name → required payload
fields; `type` and `event_id` are universal). Kind-specific extras (e.g.
`operator_decision` kind=`settle` needs `item_id` + `text_hash`) are validated
by the matching row's guard.

Genesis: `packet_frozen`.
Attempt: `pass_attempt_started`, `leg_dispatched`, `leg_result_received`,
`leg_repair_dispatched`, `leg_repair_result`, `leg_degraded`, `leg_timed_out`,
`leg_failed`, `leg_retry_dispatched`, `attempt_terminated`.
Operator surface: `gate_opened`, `operator_decision` (kinds `signoff`,
`settle`, `decide` — the CLI-only operator-gate channel, frozen decision),
`settlement_reopened`, `disagreement_raised`.
Lineage / binding / pin: `tier_escalated`, `plan_content_changed`,
`engine_pin_updated` (declared always-rejected — see §5),
`packet_pin_migrated`.
Authorization: `authorization_granted`, `authorization_revoked`,
`authorization_epoch_invalidated`.
Operator records: `operator_record_consumed` — the durably-flushed
single-use consumption RESERVATION under this packet's event-head
serialization (away-grant coordination item #4: the event is the sole
consumption authority; the sqlite index is cache/lock only). Its
fold projection is `consumed_records` on the state vector; the
`record_not_yet_consumed` guard plus log-layer event-id idempotency
make crash recovery unable to resurrect or duplicate an invocation.
`operator_record_invoked` — the policy-bound invocation record
(`RecordPolicy.invocation_record_type`, e.g. the away-gate's
`grant_invoked`), CO-APPENDED with that reservation in the SAME
expected-head CAS commit: one linearization point, one exactly-once
commit, never a second independent append (away-gate
`acceptance:single-use-race`). Its projection is `invoked_records`,
and the `invocation_bound_to_consumption` guard rejects it whenever
its record id is not already consumed — so a standalone or
out-of-order invocation append can never fold.
Sentinel: `__corrupt_tail__` (log-reader marker, not a transition — §5).

## 4. Transition table

Rendered from `TRANSITIONS` (30 rows; parity-tested cell-for-cell). Guards and
effects are NAMED entries in the `GUARDS` / `EFFECTS` registries — transition
preconditions and effects live in declarative data.

| # | Row | Event | Preconditions | Guard | Effects |
|---|-----|-------|---------------|-------|---------|
| 1 | `freeze_packet` | `packet_frozen` | `packet_lifecycle` ∈ {absent} | `freeze_payload_valid` | `init_from_freeze` |
| 2 | `start_attempt` | `pass_attempt_started` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {idle, terminal_content_verdict, terminal_operator_gate, terminal_harness_fault} | `attempt_number_matches` | `revoke_if_granted`, `begin_attempt` |
| 3 | `dispatch_leg` | `leg_dispatched` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {admitted, in_review}<br>`leg.phase` ∈ {pending, unassigned} | — | `set_leg_in_flight`, `mark_in_review` |
| 4 | `leg_result_valid` | `leg_result_received` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {in_flight} | `payload_valid_true` | `set_leg_usable` |
| 5 | `leg_result_invalid` | `leg_result_received` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {in_flight} | `payload_valid_false` | `set_leg_invalid` |
| 6 | `dispatch_repair` | `leg_repair_dispatched` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {invalid} | `repair_budget_available` | `set_leg_repair_in_flight` |
| 7 | `repair_result_valid` | `leg_repair_result` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {repair_in_flight} | `payload_valid_true` | `set_leg_usable` |
| 8 | `repair_result_invalid` | `leg_repair_result` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {repair_in_flight} | `payload_valid_false` | `set_leg_degraded` |
| 9 | `degrade_leg` | `leg_degraded` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {invalid} | — | `set_leg_degraded` |
| 10 | `timeout_leg` | `leg_timed_out` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {in_flight, repair_in_flight} | — | `set_leg_timed_out` |
| 11 | `fail_leg` | `leg_failed` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {pending, in_flight, repair_in_flight} | — | `set_leg_failed` |
| 12 | `retry_leg` | `leg_retry_dispatched` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review}<br>`leg.phase` ∈ {timed_out, failed} | `retry_budget_available` | `set_leg_retry_in_flight` |
| 13 | `terminate_content_verdict` | `attempt_terminated` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review} | `outcome_content_verdict_with_quorum` | `record_terminal_content_verdict` |
| 14 | `terminate_operator_gate` | `attempt_terminated` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {in_review} | `outcome_operator_gate_with_open_gate` | `record_terminal_operator_gate` |
| 15 | `terminate_harness_fault` | `attempt_terminated` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {admitted, in_review} | `outcome_harness_fault_with_fault` | `record_terminal_harness_fault` |
| 16 | `open_gate` | `gate_opened` | `packet_lifecycle` ∈ {active} | `gate_id_fresh_and_known_type` | `add_open_gate`, `revoke_if_granted` |
| 17 | `append_away_receipt` | `away_gate_receipt` | `packet_lifecycle` ∈ {active} | `away_receipt_record_type` | `append_receipt` |
| 18 | `signoff_gate` | `operator_decision` | `packet_lifecycle` ∈ {active} | `decision_signoff_on_open_gate` | `satisfy_gate` |
| 19 | `settle_items` | `operator_decision` | `packet_lifecycle` ∈ {active} | `decision_settle_valid` | `apply_settlement` |
| 20 | `decide_disagreement` | `operator_decision` | `packet_lifecycle` ∈ {active}<br>`disagreement` ∈ {open} | `decision_decide_valid` | `resolve_disagreement` |
| 21 | `raise_disagreement` | `disagreement_raised` | `packet_lifecycle` ∈ {active}<br>`disagreement` ∈ {none, operator_decided} | — | `open_disagreement`, `revoke_if_granted` |
| 22 | `reopen_settlement` | `settlement_reopened` | `packet_lifecycle` ∈ {active} | `item_currently_settled` | `apply_reopen`, `revoke_if_granted` |
| 23 | `escalate_tier` | `tier_escalated` | `packet_lifecycle` ∈ {active}<br>`tier` ∈ {tier_l} | — | `mark_superseded_by_successor`, `revoke_if_granted` |
| 24 | `plan_changed` | `plan_content_changed` | `packet_lifecycle` ∈ {active} | — | `mark_plan_stale`, `revoke_if_granted` |
| 25 | `migrate_pin` | `packet_pin_migrated` | `packet_lifecycle` ∈ {active} | `migration_approved` | `apply_pin_migration`, `revoke_if_granted` |
| 26 | `grant_authorization` | `authorization_granted` | `packet_lifecycle` ∈ {active}<br>`attempt_phase` ∈ {terminal_content_verdict}<br>`authorization` ∈ {none, revoked}<br>`plan_binding` ∈ {bound_current} | `authorization_preconditions` | `apply_grant` |
| 27 | `revoke_authorization` | `authorization_revoked` | `authorization` ∈ {granted} | — | `apply_revoke` |
| 28 | `epoch_invalidate` | `authorization_epoch_invalidated` | `authorization` ∈ {granted} | — | `apply_revoke` |
| 29 | `consume_operator_record` | `operator_record_consumed` | `packet_lifecycle` ∈ {active} | `record_not_yet_consumed` | `consume_record` |
| 30 | `record_operator_invocation` | `operator_record_invoked` | `packet_lifecycle` ∈ {active} | `invocation_bound_to_consumption` | `record_invocation` |

Semantics worth calling out:

- **Invalidation is a shared effect.** Every event that changes an input of
  the authorization conjunction while a grant stands carries
  `revoke_if_granted` ("revoked by any later invalidating event" — frozen
  decision): re-run (row 2), new gate (16), disagreement (20), settlement
  re-open (21), escalation (22), plan edit (23), pin migration (24).
- **Retry vs late result.** A deliberate `leg_retry_dispatched` is a NEW model
  call, lawful within budget (row 12). A late `leg_result_received` for a
  timed-out/failed/settled leg matches NO row and is rejected — INV3.
- **Escalation quiesces the predecessor.** Every mutating row except
  revocation (26/27) requires `packet_lifecycle = active`, so a Tier-L packet
  superseded mid-attempt rejects all further leg/terminal/gate work — the
  Tier-S successor (its own packet, `lineage_role = escalation_successor`)
  owns everything from its own freeze. An attempt in flight at escalation time
  is abandoned-by-supersession; the escalation event itself is the packet's
  terminal disposition for telemetry (Phase-2 wiring counts it).
- **Packet-affine pins.** `engine_pin_updated` (the global deployment event)
  is DECLARED ALWAYS-REJECTED as a packet mutation — a review started under
  pin A completes under pin A (acceptance:pin-affinity). Only an explicit,
  approved `packet_pin_migrated` moves a packet's pin, and it revokes any
  standing grant.
- **Receipts are observations, not authorization.** `away_gate_receipt` is
  the away-gate's KERNEL-AUTHENTICATED observation carrier (`grant_denied` /
  `grant_expiry_observed`, distinguished by the event's `record_type`). It is
  authenticated by log integrity — event-head CAS + hash chain — never by a
  user signature, because it records a system observation rather than a user
  authorization. The `away_receipt_record_type` guard binds `denied` to
  `grant_denied` and `expiry_observed` to `grant_expiry_observed` before
  `append_receipt` dedupes by the distinct
  (`grant_id`, `outcome`, `reason`) triple, and a receipt never changes
  authorization state. So a flood of identical denials cannot grow the
  projection, while a denial carrying a genuinely new reason still records.
- **The grant row re-checks everything.** `authorization_preconditions`
  re-evaluates the full conjunction at fold time, so a crafted
  `authorization_granted` event appended to the log can never reach `granted`
  unlawfully — fold-level fail-closed, beneath the Phase-3 authorize CLI.
- **Verdict tokens** are the verbatim v1 `recommend()` vocabulary: `SHIP`,
  `SHIP-WITH-ACCEPTED-RISK`, `NEEDS_OPERATOR_SCOPE_SETTLEMENT`, `ITERATE`,
  `RETHINK`, `BLOCKED`. Which tokens (and which operator records) permit
  authorization is Phase-3 authorize policy layered ON TOP of this model; the
  state machine only enforces that a content verdict exists, quorum held, and
  no gate/settlement/disagreement/binding condition is violated.

## 5. Rejection semantics

Accepted transitions are distinguished from rejected events, and **rejection
never mutates valid state** (INV6, replay-proven):

1. **Default-reject.** An event matching no row is rejected with a reason
   (`no_transition_defined`, a `precondition_failed:<dim>=<value>`, or a guard
   reason). The fold records `(event_id, type, reason)` and state is
   byte-identical.
2. **Declared always-rejected.** `engine_pin_updated` has no row BY DESIGN
   (`DECLARED_ALWAYS_REJECTED`); the exhaustiveness test pins that it never
   gains one accidentally.
3. **Payload rejection.** Unknown event types, missing required fields, and
   unknown leg ids reject before table dispatch (`unknown_event_type`,
   `payload_missing_fields:...`, `payload_unknown_leg`).
4. **Duplicates are idempotent (INV4).** A re-delivered `event_id` is recorded
   in `FoldResult.duplicates` and never re-applied.
5. **Corrupt tail halts.** The `__corrupt_tail__` sentinel (emitted by the log
   reader for an undecodable/truncated tail) stops the fold; nothing after it
   is processed; `integrity = corrupt_tail` denies authorization as a
   retryable harness fault.
6. **Ambiguity is a table defect.** If two rows ever matched one (state,
   event) instance the fold raises `AmbiguousTransitionError`; the
   exhaustiveness suite proves this unreachable for the shipped table.

## 6. Machine-checkable invariants

Implemented in `review_kernel/invariants.py`; enforced across the scenario
corpus, the property traces, and the mutation kill suite.

| Id | Statement | Where checked |
|----|-----------|---------------|
| INV1 | **Exactly one pass outcome.** At most one terminal record per attempt; record shape matches its outcome; a second terminal for the same attempt is rejected. | `state_invariant_violations` + replay step check |
| INV2 | **Authorization soundness.** `authorization = granted` ⇒ packet active ∧ eligibility `eligible` ∧ pin `approved` ∧ `plan_binding = bound_current` ∧ current attempt terminal-content-verdict ∧ quorum(tier) ∧ no open gates ∧ no reopened settlements ∧ no open disagreement ∧ binding recorded with matching plan hash. | `state_invariant_violations` + `authorization_preconditions` guard |
| INV3 | **Late results cannot revive.** An applied leg (repair) result always found its leg `in_flight` (`repair_in_flight`); results for timed-out/failed/settled legs are rejected. | replay step check |
| INV4 | **Duplicates idempotent.** Re-delivering any already-seen event changes nothing. | `duplicate_idempotence_violation` |
| INV5 | **Escalation invalidates.** A superseded packet never holds `granted`; Tier-L→Tier-S escalation revokes earlier authorizing state. | `state_invariant_violations` |
| INV6 | **Rejection never mutates.** State fingerprint unchanged across any rejected or duplicate event. | replay step check |
| INV7 | **Terminal attempts are immutable.** No leg event lands once the current attempt is terminal. | replay step check |
| INV8 | **Attempt monotonicity.** `attempt_number` moves only by +1 and only via `pass_attempt_started`. | replay step check + `attempt_number_matches` guard |

## 7. Property-based trace tests

`tests/test_trace_properties.py` runs a seeded, deterministic,
dependency-free property harness (150 seeds × 50 steps): random walks over a
state-aware probe alphabet with adversarial injections — **duplicate**
deliveries, **reordered/late** replays of stale events, **corrupt** payloads
and unknown types, **retry** storms, **escalation**, **rollback**
(epoch invalidation), and **pin-update** attempts. Each required trace family
is additionally pinned by a deterministic scenario in
`tests/kernel_scenarios.py` so coverage never depends on seed luck. Properties:
determinism, incremental-fold ≡ batch-fold, all replay + state invariants,
duplicate idempotence, and corrupt-tail halting. A canary test asserts the
walks actually reach deep states (grants, supersession, terminals, corrupt
tails) so a generator regression cannot silently hollow out the battery.

## 8. Mutation testing

`tests/test_mutation_kills.py` generates one mutant per: **dropped row**,
**widened precondition** (a require entry replaced by its full domain),
**stripped guard**, **dropped effect** — 149 mutants for the 30-row table —
and requires the kill suite (full scenario corpus + invariant battery + a
fixed set of property seeds) to FAIL on every one. There is no survivor
allowlist; a baseline test proves the kill suite passes on the unmutated
table, and a generator-shape test fails if the mutant count collapses.

## 9. Exhaustiveness and dead ends

`tests/test_transition_exhaustiveness.py` proves: every event type has a row
XOR is declared always-rejected; every guard/effect/dimension reference
resolves; every require value is inside its dimension's domain; `decide()` is
total and unambiguous over every reachable state × the full probe alphabet;
and **no reachable state is a dead end** — every non-quiescent state accepts
at least one probe, where quiescence is exactly the declared terminal
condition (superseded packet with no standing grant). The initial state
accepts only `packet_frozen`.

## 10. Deferred bindings (later phases; declared seams)

- **Envelope / linearization** (Phase 1 kernel core): sequence numbers,
  prev-head checksums, expected-head CAS, sqlite index. The fold consumes
  decoded events; the `__corrupt_tail__` sentinel is the layer boundary.
- **Attestation** (Phase 3, acceptance:attestation): operator-decision events
  will carry a verifiable user-presence attestation binding packet, snapshot,
  plan hash, event head, gate/item ids, decision, nonce, timestamp. The state
  model already routes ALL operator decisions through `operator_decision`
  events so the attestation check slots into intake validation without new
  states.
- **Away-grant records** (Phase 3, acceptance:grant-evaluation): grant-type
  operator records evaluate generically by data with fold-derived lifecycle;
  tier-graded targets (Tier L lineage-root + SHIP, Tier S exact-sha) bind at
  the authorize layer. `authorization_binding` is the extension point.
- **Scope manifest / trigger corroboration** (Phase 3,
  acceptance:scope-manifest): `manifest_hash` is already frozen and bound by
  the grant guard; the manifest generator and implement-gate enforcement land
  with the authorize CLI.
- **Verdict-token authorize policy** (Phase 3): which tokens + operator
  records authorize per tier.
- **Telemetry classification** (Phase 2, goal-telemetry): outcome-class /
  phase-duration fields ride the same events (`driver`, `operator_present`,
  `outcome_class`, `pause_class`); abandonment-by-supersession is counted
  from the escalation event.
