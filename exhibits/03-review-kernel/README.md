_Provenance: original location `<agent-skills>/skills/review-kernel/README.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized._
_Content otherwise unchanged._

# review-kernel — plan-review v2 kernel (LIVE)

The v2 replacement for the `cross-agent-review-converge` engine, built per the
authorized plan `<agent-skills>/docs/plans/plan-review-v2-kernel.md` (operator
implementation authority granted 2026-07-22, recorded in the plan's step 0.2).

**Status: LIVE (provisional cutover).** Activated at epoch
`v2-cutover-20260723T170500Z` (2026-07-23); the v1 engine is retired and its
state root frozen. FINAL ship acceptance follows the reactive observation
window (health check `review_kernel.observations`).

**The callable surface is the sibling skill `<agent-skills>/skills/plan-review/`**
(Codex) and `<claude-config>/commands/plan-review.md` (Claude Code) — this directory
is the ENGINE, not a skill, and deliberately has no `SKILL.md` of its own.
Both installed surfaces are byte-identical bodies installed from
`surfaces/plan-review.{claude,codex}.md` here; `tests/test_surface_parity.py`
fails on drift or missing frontmatter.

Operational runbook: `docs/cutover-runbook.md`.

## Layout

- `docs/state-model.md` — the formal state-model deliverable
  (acceptance:state-model): every state dimension, the declarative transition
  table, rejection semantics, machine-checkable invariants, and the testing
  contract. Reviewable artifact; parity-tested against the code.
- `review_kernel/` — the kernel package (counts against the ≤4,500-LOC core
  budget): `events.py`, `state_model.py`, `transitions.py`, `effects.py`,
  `fold.py`, `invariants.py`, `faults.py`, `eventlog.py` (append-only
  `events.jsonl` under the linearization contract: monotonic sequence,
  prev-head checksum chain, expected-head CAS, per-packet lock, fsync before
  index update), `index.py` (disposable derived sqlite index — never
  authoritative), `packets.py` (content-addressed freeze + the kernel
  state-read API + the CAS `record_events` wrapper).
- `review_kernel/transport.py` — the transport SEAM: request/outcome shapes,
  the `Transport` protocol, the v1-verbatim 256 KiB request ceiling + review-
  body budget clamp, and the pure leg-outcome classification (v1 reviewer
  normalization at the data boundary). Phase 2's direct-sqlite transport and
  the acceptance:concurrency-soak harness implement/reuse this seam.
- `review_kernel/transport_sqlite.py` — the Phase-2 direct-sqlite leg
  transport (engine-as-owner): writes `consult_advisor` rows against the
  shared task store (kind `worker_consult`, `delivery_mode='internal'`,
  `source='engine_owner'` provenance, legacy advisory mode — question in the
  row summary), with the bounded write-retry contract and idempotent
  re-submit (acceptance:transport-hardening), and reads terminal rows back
  into seam `LegOutcome`s (quota vs transport-error classification; missing
  verdict JSON → the schema-invalid repair path). Enforces the legacy-safety
  invariant: engine rows must not trip the trusted-v2 envelope marker until
  the `engine_owner` allowlist companion fix lands. INACTIVE — nothing
  dispatches against the live store until cutover (acceptance:ship-order).
- `review_kernel/admission.py` — cross-process admission control
  (acceptance:admission): the one data-configured per-provider capacity
  semaphore (concurrency + rolling-rate budgets) over short sqlite lease
  transactions in the engine's OWN `admission.sqlite` (never the shared task
  store). Visible waits (position/tier/age, never a failure token), bounded
  deadlines expiring as retryable `provider_quota` faults, lease expiry +
  crash recovery reclaimed by any process's poll, cancellation, strict
  oldest-first fairness (starvation-free by construction), and the exact
  per-tier model-call budgets derived from the kernel's repair/retry
  constants and enforced fail-closed at request time.
- `review_kernel/provenance.py` — per-review provenance as recorded fact
  (goal-provenance): the immutable `provenance.json` freeze-time record
  (source repo/commit/plan path, read mode, engine pin); Tier-S
  pinned-commit plan reads via `git show` (goal-tiering — reviewed bytes
  cannot move under the reviewer; Tier L reads live but still records the
  resolved commit); packet-affine pin execution (`assert_packet_affinity` —
  frozen "Pin compatibility rule", proven across a mid-flight pin update per
  acceptance:pin-affinity at kernel scope); and the pin-update review gate
  (`verify_deployment_ref` — the engine refuses an unpinned or unreviewed
  deployment ref, acceptance:transport-hardening named test).
- `review_kernel/deploy.py` — the pinned-deployment install for the engine
  itself: a detached worktree of the engine's source repo at an exact commit
  + a `deployment.json` marker; `verify_pinned_engine` is the executable
  deployment probe (marker/HEAD/clean-tree; acceptance:ship-order step 1),
  `engine_self_check` composes it with the pin-update review gate — a
  running engine refuses a missing, moved, dirty, or unreviewed deployment.
  Same-pin re-install is idempotent; a drifted deploy dir refuses (explicit
  removal is the migration path, never silent re-pin).
- `engine/repository_evidence.py` — bounded source evidence for tool-disabled
  reviewers. It selects only manifest paths named in the bounded question,
  a verified predecessor's open findings, or repeated `--evidence-path`
  flags; reads exact Git blobs from the recorded commit; refuses traversal,
  symlinks, non-blobs, binary/non-UTF-8 source, and size overflow; and binds
  the canonical sidecar digest into the existing snapshot and packet ids.
  A predecessor is accepted only when its canonical v2 ledger digest is bound
  to its terminal event and its body, evidence, provenance, event log, and
  retained reviewer results all pass packet-integrity checks. Usable results
  and terminal verdicts use atomic packet-local writes. A persistence failure
  becomes a retryable terminal harness fault rather than a stranded in-flight
  packet, including JSON serialization and non-finite-number failures.
  If the authoritative event log itself cannot append the terminal fault,
  the operation raises and the nonterminal packet is abandoned; it is never
  treated as resumable or authorizing, and recovery uses a newly frozen packet.
  The first-attempt append is preceded by a durable dispatch-poison marker that
  is removed only after the event lands, closing the otherwise-idle ambiguity.
  If that cleanup fails after the event is committed, dispatch preflight removes
  the provably stale marker durably while holding the activation lock; an idle
  packet with a marker is always refused. A packet-persistence fault also stops
  Tier-L fallback before a second reviewer can be dispatched.
  `dispatch-existing` accepts either a pristine
  freeze-only packet or a terminal harness fault explicitly marked retryable;
  it still refuses corrupt logs, content re-review, and non-retryable faults.
  Retry reconstructs the original predecessor-derived review pass and P1
  callout eligibility before dispatch, so verdict policy cannot reset.
  Result filenames include their content digest, so an unbound artifact from
  a late durability failure cannot overwrite an earlier event-bound result.
  Malformed custom synthesis output or an unknown verdict token becomes a non-retryable
  engine-internal terminal harness fault when the event log remains writable.
- `engine/activation_update.py` — crash-recoverable active-pin changes. It
  serializes updates with a kernel file lock, journals path-bound exact
  preimages and old/new hashes, stages an approved candidate before switching
  the activation pointer, and recovers before either engine entrypoint touches
  a packet. Every run/freeze/dispatch holds that same lock from recovery
  through completion, excluding an activation switch during engine work. The
  production SQLite transport additionally requires a guard-issued live lock
  lease at construction and rechecks it at each dispatch and collect. Leases
  are registered only inside the lock context and are invalidated on exit, so
  supported direct runner calls and transport wrappers cannot bypass the
  boundary; `activation_switched` leases refuse transport until the freeze-only
  smoke completes. This guards supported local entrypoints, not hostile Python
  deliberately importing private in-process state.
  Activation requires an explicit operator-approval reference plus
  an intact terminal `SHIP` packet that reviewed the exact candidate commit
  and the explicitly named authority plan. The authority verifier also
  re-hashes every event-bound reviewer result and requires the canonical
  findings-ledger digest to match the terminal event before switching.
  Rollback verifies the retained old pin before mutation and journals
  `rollback_in_progress` before restoring the pointer, so a failed restore is
  retried deterministically on the next guarded entry. A pending activation
  permits only a freeze-only smoke; that pass
  verifies caller-bound activation/pins path affinity plus its intact idle
  event log, body, evidence, identity, candidate pin, pinned read mode, and
  exact candidate source commit before rebuilding the evidence from that
  resolved Git object and
  it marks the transaction verified. The retained old pin is the rollback
  target. The pending candidate has already passed pre- and post-switch engine
  self-checks; with dispatch blocked, no invocation means no engine work. No
  background expiry daemon is part of this bounded local repair. The CLI
  requires `--approved-by` only for `activate`; deterministic
  `recover` and `rollback` never demand fabricated approval text.
- `review_kernel/operator_records.py` — the signed operator-record envelope
  + generic evaluator (Phase 3; **interface `opr-1`**, the frozen contract
  the Operator-Authorization-Gate away-grant lane binds to per its
  coordination items #4/#6/#13). SSH-signature attestation (`ssh-keygen -Y`,
  no new dependency; user presence is a property of the enrolled key type —
  the plan's open mechanism choice); principal derived from the
  allowed-signers match, never a payload claim; binding fields per
  goal-authorization-trust with the full acceptance:attestation negative set
  at the record layer (unattested / tampered / unknown-signer / copied /
  replayed-nonce / stale-head / scope-changed all deny). Generic lifecycle
  per acceptance:grant-evaluation: policy registration (`with_policy`) is
  the away-gate extension point; revoke is a separate attested record;
  expiry derives at eval; an unreadable stream denies; `allowed_principal`
  enforced against the consuming context. Single-use consumption is the
  `operator_record_consumed` RESERVATION EVENT under the packet head CAS
  (transition row 28; the log is the sole consumption authority — the sqlite
  index is cache/lock only; crash recovery can neither resurrect nor
  duplicate an invocation).
- `review_kernel/scope_manifest.py` — the machine-generated scope manifest
  (acceptance:scope-manifest; tier inputs are non-self-attested): the
  generator derives canonical root/paths, registry classes, and
  architecture-trigger hits FROM the proposed write set (an omitted registry
  path is caught by construction); `corroborate_tier_declaration` refutes a
  false Tier-L declaration on any trigger hit (escalates, never authorizes
  at L); `manifest_hash` binds into the kernel's INV2 authorization
  conjunction; and `check_write_allowed` is the implement-gate guard —
  alias/symlink-resolved containment, newly-encountered Tier-S triggers
  under Tier-L fail closed, and a superseded (escalated) packet rejects all
  writes. Trigger patterns are data (`DEFAULT_ARCHITECTURE_TRIGGERS`).
- `review_kernel/cli.py` — the kernel CLI (`python -m review_kernel.cli`):
  `signoff`/`settle`/`decide` are the ONLY channels writing operator-decision
  records (sign → verify → write record → append event with `record_ref`;
  a bad/missing key persists NOTHING), and `authorize` is the fail-closed
  boundary: it re-audits acceptance:gate-integrity on every call (each
  gate-satisfying event must trace to an attested record whose signature
  verifies and whose bound `event_head` equals the event's own `prev_head`
  in the chain — hand-authored files, hand-appended events, replayed
  records, and ledger prose all deny), optionally evaluates + consumes an
  away grant (policy supplied as data via `--grant-policy-json`), then
  appends `authorization_granted` for the kernel's INV2 guard to re-check
  event-side. `--check` audits without appending. Dependency-thin: the
  hook-guard repoint targets this module; the instruction-line flips ride
  the operator-reviewed amendment window (step 4).
- `tests/` — hermetic by default (an autouse fixture blocks all network):
  scenario corpus, transition exhaustiveness, seeded property-based trace
  tests, mutation testing, doc parity, event-log linearization, LOC-budget
  gate; `fake_transport.py` (FakeTransport + LegDriver — scripted model
  latency / invalid-response repairs / transport retries / quota responses /
  silent hangs over a virtual clock, driving the kernel through the REAL
  log + fold path) with `test_fake_transport_lifecycles.py`; and the
  inherited requirement-contract suite (acceptance:contract-tests)
  re-expressed from v1 at the v2 data boundary:
  `test_contract_verdict_semantics.py`,
  `test_contract_reviewer_normalization.py`,
  `test_contract_scope_fidelity.py`, `test_contract_lint.py`,
  `test_contract_settlement_gates.py`, `test_contract_transport_budget.py`,
  `test_contract_citation_security.py`. Contract tests whose v1 originals
  need Phase-2/3 surfaces (packet composer, provenance module, CLI intake)
  carry explicit `deferred` skip markers naming the v1 test — never fakes.

## Running a review pass

`python -m review_kernel.cli` is the OPERATOR-GATE boundary only
(`signoff` / `settle` / `decide` / `authorize`). Driving a pass lives in the
engine package, because it needs `engine.runner` and `review_kernel/` may
neither import `engine` (tests/test_engine_boundary.py) nor grow
(acceptance:kernel-size is at its cap):

```bash
cd <agent-skills>/skills/review-kernel
python3 -m engine.cli run-pass \
  --plan docs/plans/<slug>.md --repo <workspace> \
  --question "<the bounded ask>" \
  --write-set scripts/foo.py --write-set docs/bar.md \
  [--tier tier_s|tier_l] [--dry-run|--freeze-only]
```

It composes the shipped layers in the order they already ran when passes
were hand-written — freeze (with provenance) → tier corroboration →
admission → engine-owner transport → `EngineRunner.run_attempt` — and
changes no dispatch semantics. What it adds:

- **The engine pin and packets root are read from the live activation
  manifest** (`<state>/review-kernel/activation.json`, or
  `$REVIEW_KERNEL_ACTIVATION`), and the pin is re-checked against the
  approved-pin registry before anything is frozen. Never hardcoded.
  `$REVIEW_KERNEL_PACKETS_ROOT` overrides the root, same variable the
  detector honours.
- **The review body is assembled, not improvised** (`engine/review_body.py`):
  packet identity + pinned repository evidence + contract sections + question
  + the frozen plan, checked against the v1 transport budget. Oversize FAILS
  LOUD — it never truncates a plan or evidence. The contract is read in place
  from the v1 prompt corpus
  (`skills/cross-agent-review-converge/prompts/review_contract.md`); the v2
  cutover retired the v1 engine, not its prompts. `--contract` overrides.
- **`body_for_leg` is a required `EngineRunner` argument.** It used to
  default to a two-word placeholder — a test seam in a production-capable
  constructor, so a caller who forgot the body dispatched a real reviewer
  leg that reviewed nothing and still returned a verdict. Omitting it now
  fails at construction.
- **Irreconcilable freeze inputs refuse before a packet exists.** A
  malformed Guardrail Header forces Tier L; an architecture-trigger hit
  forces Tier S. When both fire, `run-pass` refuses with the reason instead
  of creating a packet the kernel then rejects.
- `--dry-run` derives and reports everything (tier, manifest hash, header,
  body size) while freezing no packet and dispatching no leg;
  `--freeze-only` freezes and writes `review-body.md` / `scope-manifest.json`
  / `provenance.json` / `repository-evidence.json` sidecars but still
  dispatches nothing. `dispatch-existing` revalidates the sidecar, body,
  provenance, packet identity, and active pin before transport.

With no `--write-set`, the manifest covers only the plan file — conservative
by construction, since every implement-gate write outside it fails closed.

## Running the suite

```bash
cd <agent-skills>/skills/review-kernel && uv run --with pytest python -m pytest tests/ -q
```

## Pre-merge gate

A green suite gates merges (acceptance:contract-tests). The local pre-push
hook mirrors the converge-suite gate — install (idempotent; chains any prior
hook, including the converge gate, as
`pre-push.prelocal.review-kernel-gate`):

```bash
bash <agent-skills>/skills/review-kernel/scripts/install_review_kernel_suite_pre_push.sh
```

`--check` reports `installed` / `not-installed`. Fail-CLOSED only on a
confirmed red suite; fail-OPEN (loud warning) on missing tooling. See
`docs/premerge-gate.md`.
