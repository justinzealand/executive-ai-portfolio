_Provenance: original location `delegated-work repo, docs/overnight-detect-and-fix.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized; Slack channel names generalized._
_Content otherwise unchanged._

# Overnight detect-and-fix architecture

Durable architecture doc for the overnight detect-and-fix orchestrator. Reference
for future auditors, operators, and cross-runtime skill authors — this is NOT
the plan (plan authority: `docs/plans/overnight-detect-and-fix.md`; live-mode
child plan: `docs/plans/overnight-live-mode-activation.md`).

## Purpose

Every morning, the platform's surfaces meet the charter's C4 definition of green
— *no unhandled operator-actionable item outside its service-level window, no
health WARN older than 24 hours without an active repair path, no CRITICAL item
lacking either completed remediation or an explicit needs-operator route* —
because overnight the system detected each non-green signal from existing
trusted sources, classified it against charter tripwires, and acted
autonomously on the auto-fixable class. Operator interventions trend toward the
charter's ≤1/week gate (Success Signal 4) without expanding what runs
autonomously beyond what the charter sanctions.

## Topology (22:30 America/New_York)

```
   OC cron (projects-daily-closeout, 22:30 ET)
       │
       ▼
   sidecar workflow projects_daily_closeout (this repo)
       │  … existing closeout work (repo sync, roadmap sweeps, snapshots) …
       │
       └── _invoke_overnight_orchestrator(report)   ← the sidecar HOOK
              │
              │ sys.path.insert(0, <workspace>/scripts)
              │ import overnight_sidecar_wire_in
              ▼
       run_overnight_orchestrator_sidecar(mode=…)   ← the ONE public entrypoint
              │
              ├── Phase 1  verifier      (previous night's merged PRs)
              ├── Phase 2  adapters      (4 signal sources)
              ├── Phase 3  classify+gate+dispatch
              ├── Phase 4  metrics + heartbeat  (ALWAYS attempted)
              └── Phase 5  morning digest  (the Slack alerts channel)
```

NO separate launchd job. NO separate OC cron. NO separate OpenClaw skill. See
§Shrinkage-signal accounting for why.

## Signal adapters (Phase 2)

Four adapters — one per trusted signal source — each returns an
`AdapterResult(signals: list[Signal], error: str | None)`. All errors are
adapter-local ("total source failure" = 4-of-4 adapters returned an error, only
then does the run halt).

| Adapter                           | Source                                                      | Reads                                                                     |
|-----------------------------------|-------------------------------------------------------------|---------------------------------------------------------------------------|
| `load_daily_health_signals`       | `<openclaw-config>/health/daily-health-check-<date>.json`         | Every `checks[]` row whose severity is not `ok`                           |
| `load_watchdog_signals`           | `<openclaw-config>/watchdog/incidents.json`                       | Every open (unresolved) incident                                          |
| `load_closeout_signals`           | `<openclaw-config>/langgraph/history.db` (`runs`+`run_outputs`)   | The most recent `execute`-mode `projects_daily_closeout` outcome          |
| `load_orphan_signals`             | `<state>/automation-state/orphan-observations/queue.sqlite`      | Every open (non-self-healed) row past its 6h grace window                 |

Each signal carries `check_id`, `source_of_record_identifier` (the primary key
the verifier grades against next morning), `subject` (the alert headline), and
timestamps. Adapters never mutate their source — read-only by contract.

## Classifier (Phase 3a)

`overnight_orchestrator.classify_signal(signal, manifest)` maps a signal to one
of:

- `auto_fix_eligible` — a signal-policy-manifest row named this `check_id` +
  `source_of_record_identifier` shape as auto-fix-able AND named the adapter
  (`sanctioned_adapter`) that owns the fix.
- `needs_operator` — the manifest explicitly routes this class to operator
  attention.
- `wait_and_see` — the manifest names an aging threshold before escalation.
- `unrouted` — the classifier saw no matching manifest row. Routed to
  `needs_operator` with reason `unrouted_by_manifest` (fail-safe: never
  auto-fix a signal the manifest didn't name).

The manifest is a signed JSON policy at
`<workspace>/delegated-work/config/overnight_orchestrator/signal_policy_manifest.json`.
Its `manifest_sha` is stamped into every metrics row for provenance.

## Pre-action gate (Phase 3b)

`overnight_pre_action_gate.evaluate(classified_signals, ...)` reads the
**registry baseline** for the five charter B2 mandatory-consumer registries:

- `<workspace>/ALLOWLIST.md`
- `<workspace>/EXCEPTIONS.md`
- `<workspace>/SURFACES.md`
- `<workspace>/ALWAYS_ON.md`
- `<workspace>/KNOWN_CHRONIC.md`

For each `auto_fix_eligible` signal, the gate confirms every registry row the
manifest declared REQUIRED for that adapter is present in the current baseline.
The baseline is a per-run snapshot committed to
`<state>/automation-state/overnight-orchestrator/registry-baseline.json` at run start
— so a mid-run registry mutation cannot re-authorize dispatch (a "trust drift
introduced by our own mutation attempt" defense, same shape as
`backlog_executor.py`'s policy-manifest snapshot).

**Run-halt classes** (short-circuit the pipeline, land as ONE
`degraded:run_halt:<class>` row on `WireInRunResult.degraded_rows`):

- `total_source_failure` — 4-of-4 adapters returned an error.
- `manifest_corrupt` — the signal-policy manifest failed to parse.
- `registry_total_drift` — the five-registry baseline differs from the shipped
  live copies at run start (a signed-config-change tripwire).

The verifier + metrics + heartbeat + morning digest STILL fire on a run-halt —
so a broken orchestration is observable within one daily-health cycle.

## Dispatch (Phase 3c)

`overnight_dispatch.dispatch_all(classified, state_dir, manifest, mode, now)`
walks eligible signals and:

1. Checks the CO-3 operational gate — `_check_operational_gate(state_dir,
   env)` returns `("live", …)` iff the sidecar-computed
   `_resolve_requested_mode(state_dir)` also returns `"live"`. Both use
   `Path.exists()`; both live-mode paths are aligned bytes-for-bytes with the
   sidecar's `enabled` / `disabled` / `OVERNIGHT_ORCHESTRATOR_DISABLED` check.
2. Enforces bounded caps:
   - `max_auto_fix_dispatches_per_run = 3`
   - `max_auto_fix_dispatches_per_check_id_per_7d = 2`
   - `per_action_timeout_seconds = 1800`
3. Runs each cap-passing signal through its `sanctioned_adapter` (a
   `run_overnight_adapter(kind, subject_ref)` call — the adapter opens a
   `wsroot`-owned PR against the target repo, then the sidecar-verifier grades
   the fix the following night).
4. Records outcomes in `<state>/automation-state/overnight-orchestrator/queue.sqlite`
   (queue) and `<state>/automation-state/overnight-orchestrator/in-flight-prs.jsonl`
   (verifier ledger).

**Dry-run mode** (the pre-2026-07-04 baseline and the current CI mode) exercises
Phases 2-3 with no adapter dispatch, no PR open, no Slack send — but writes the
same metrics + heartbeat + morning-digest projection so the grading loop is
unbroken.

## State queue schema

`<state>/automation-state/overnight-orchestrator/queue.sqlite`, table `queue`:

```
id                            INTEGER PRIMARY KEY AUTOINCREMENT
condition_key                 TEXT NOT NULL UNIQUE     -- {check_id}:{source_of_record_identifier}
source                        TEXT NOT NULL            -- daily_health | watchdog | closeout | orphan
check_id                      TEXT NOT NULL
source_of_record_identifier   TEXT NOT NULL
subject                       TEXT NOT NULL
first_detected_at             TEXT NOT NULL            -- UTC ISO-8601
last_detected_at              TEXT NOT NULL            -- UTC ISO-8601
disposition                   TEXT NOT NULL            -- auto_fix_dispatched | needs_operator | wait_and_see
attempts                      INTEGER NOT NULL DEFAULT 0
pr_url                        TEXT
verified_at                   TEXT
resolved                      INTEGER NOT NULL DEFAULT 0
```

Indexes on `condition_key` and `(disposition, resolved)`. Reads are `sqlite3
-readonly` (multiple concurrent readers). Writes go through
`overnight_state_queue.open_or_create` — one writer at a time (the 22:30
sidecar).

## Sign-off ledger contract

`<state>/automation-state/overnight-orchestrator/operator-sign-offs.jsonl` is an
append-only JSON-lines ledger of operator sign-offs — one line per sign-off,
each carrying:

- `sign_off_id` — the immutable key.
- `scope` — the sanctioned scope (`bounded-override` for cap bumps,
  `live-mode-arm` for the 2026-07-04 arming, etc.).
- `target_hash` — the SHA-256 of the target artifact (plan file, config file,
  charter version) at the moment the sign-off was granted; readers verify hash
  match before honoring the row.
- `granted_at` — UTC ISO-8601.
- `expires_at` — optional; a scoped sign-off may expire.

**Read-only discipline** — the sign-off ledger is read by
`overnight_orchestrator_signoff.py` and by the pre-action gate (for
`bounded-override` scopes) but written ONLY by the operator or by explicit CLI
subcommands under `overnight_orchestrator_signoff.py`. Every other module in the
stack asserts read-only-by-glob against this path
(`<workspace>/scripts/test_signoff_readonly.py`).

## Verifier (Phase 1 — runs FIRST)

`overnight_post_action_verifier.verify_pending(ledger_path, collector_dir, now,
gh_runner=None)` reads the step-6 `in-flight-prs.jsonl` ledger and, for each
`open` or `merged_pending_verify` row, grades the seed signal's next collector
observation:

| Signal ↓ / State →              | `open`                                 | `merged_pending_verify`                 |
|---------------------------------|----------------------------------------|-----------------------------------------|
| `collector_status == "ok"`      | → `resolved` (`check_cleared_ok`)      | → `resolved` (`check_cleared_ok`)       |
| Observed WARN/FAIL              | (stays `open` until GH state changes)  | → `escalated` (`post_action_verification_failed`) |
| GH `state == MERGED`            | → `merged_pending_verify`              | (idle)                                  |
| GH `state == CLOSED`, unmerged  | → `closed` (`pr_closed_unmerged`)      | (idle)                                  |
| Age ≥ 36h, not_observed         | → `escalated` (`verification_window_degraded_timeout`) | → `escalated` (`verification_window_degraded_timeout`) |

Runs FIRST in the phase order so a `resolved` PR leaves the dispatcher's
idempotency ledger before the current run makes its dispatch decisions —
otherwise a still-recorded in-flight PR would suppress a fresh dispatch of the
same class.

## Morning digest (Phase 5)

`overnight_orchestrator_morning_digest.generate_and_send_morning_digest(...)`
composes ONE structured Markdown post covering:

- auto-fix PRs opened + merged + verified (cycle-scoped from
  `verify_result.transitions`, NOT the cumulative ledger)
- needs-operator items still pending
- wait-and-see items aging past 48h
- any run-halt / degraded signal

Delivered via `openclaw message send` to the Slack alerts channel — the SAME
channel the daily-health alert already targets (SURFACES.md row reused; §Shrinkage-signal
accounting). Three-attempt retry with backoff (1.0, 2.0, 4.0)s; on
send-failure exhaustion the body persists to
`<state_dir>/undelivered-digests/<YYYY-MM-DD>-<HHMMSS>.md` and the NEXT send
prepends a `## Undelivered backlog` section listing each file.

**Quiet-morning gate** — `has_actionable_content(...)` returns False and the
sender is NEVER invoked when the night was truly quiet (no dispatches, no
verifier transitions, no needs-operator pending, no aging_expired
wait-and-see, no degraded rows, no undelivered backlog). The Pulse heartbeat is
the sole "did the run happen" signal on quiet mornings, so the actionable-only
character of an alerts channel is preserved.

**Timezone pin** — every date/time expression resolves through
`ZoneInfo("America/New_York")` (matches the 22:30 ET sidecar fire); a UTC-off-by-one
filename is impossible.

**Fail-open** — a digest exception is captured as
`condition_local:digest:exception:<Type>` on
`WireInRunResult.degraded_rows` and the sidecar returns normally. A digest
crash NEVER poisons the outer contract.

## OC-side wrapper — WHY THERE IS NONE

Parent plan §OQ-3 frozen decision: the closeout sidecar IS the sanctioned
OpenClaw-side path. The reasoning:

1. **No other launchd/OC-cron writer of overnight state exists.** Every write
   to the state queue, in-flight ledger, morning digest, metrics, and
   heartbeat flows through `_invoke_overnight_orchestrator` in the 22:30
   sidecar. Adding a separate OC skill under
   `<openclaw-config>/workspace/skills/overnight-orchestrator/` would introduce a
   second writer, opening a write-race and a "two sources of truth" hazard.
2. **The sidecar IS the OC integration.** The 22:30 `projects-daily-closeout`
   OC cron fires the sidecar workflow (`delegated-work`). The sidecar imports
   the workspace-root wire-in module. That IS the OC-side wire — no separate
   skill needed.
3. **Operator-facing invocation lives in `<claude-config>/commands/` and
   `<agent-skills>/skills/`.** These are pass-through documentation wrappers (see
   `<claude-config>/commands/overnight-orchestrator.md`,
   `<agent-skills>/skills/overnight-orchestrator/SKILL.md`), and they explicitly
   document the OC-side scope decision so a future skill-parity sweep can't
   quietly add a redundant OC copy. The parity test
   `<workspace>/scripts/test_overnight_wrapper_parity.py` asserts this.

If the invariant ever changes (e.g. a legitimate OC-only surface emerges), the
parent plan's OQ-3 needs an operator-approved amendment BEFORE authoring a new
skill — and the parity test must be updated in lockstep.

## Kill-switch precedence

`_resolve_requested_mode(state_dir)` in
`src/langgraph_sidecar/projects_daily_closeout.py` is the ONE code path that
decides live-vs-dry-run. Returns `"live"` iff ALL of:

1. `<state>/automation-state/overnight-orchestrator/enabled` exists.
2. `<state>/automation-state/overnight-orchestrator/disabled` does NOT exist.
3. `OVERNIGHT_ORCHESTRATOR_DISABLED` env var is unset or not `"1"`.

Otherwise returns `"dry-run"`. The `disabled` flag beats `enabled`; the env
var beats both flags.

- **Arm live mode**: `touch <state>/automation-state/overnight-orchestrator/enabled`
- **Disarm live mode (O(1) rollback)**: `touch
  <state>/automation-state/overnight-orchestrator/disabled`
- **Full clear**: `rm <state>/automation-state/overnight-orchestrator/enabled`
- **Kill-switch env** (per-session override): `export
  OVERNIGHT_ORCHESTRATOR_DISABLED=1`

Live mode was armed 2026-07-04 (sign-off row `bounded-override` scope at
`2026-07-04T14:51:45Z`, `target_hash=82853d82…`; child plan
`docs/plans/overnight-live-mode-activation.md`). Any PR touching this stack
MUST NOT create or remove the `enabled` flag file — a CI test asserts the
shipped codebase cannot dispatch live without the operator-created flag.

## Shrinkage-signal accounting

The charter's "shrinkage-signal" discipline says every new capability must
either RETIRE at least one existing operator surface + persistent service, or
justify the net-additional footprint against tripwires M2 (a new persistent
service auto-routes to `needs-operator` unless the sidecar-shape carve-out
applies) and M5 (a net-additional operator surface auto-routes unless paired
with retirement).

**M2 — persistent-services delta**: **0 rows added to
`<workspace>/ALWAYS_ON.md`.** The orchestrator runs INSIDE an existing OC cron
(`projects-daily-closeout`, a row that predates this work). The sidecar-shape
carve-out at charter M2 says an inline extension of an already-tripwired
service does not count as a new persistent service — that is exactly this
shape. No new launchd job, no new OC cron, no new daemon.

**M5 — operator-surface delta**: **0 rows added to `<workspace>/SURFACES.md`.**
The morning digest reuses the existing Slack alerts-channel row (the same
channel the deterministic health-alert sender already writes to). No new
Slack channel, no new dashboard, no new alert stream. Existing SURFACES.md row
inherits.

Together these zeros mean this capability's net footprint is:

- +1 python module (`overnight_sidecar_wire_in.py`) + supporting stack
- +1 arch doc (this file) + +2 wrapper docs
- +1 sidecar hook (`_invoke_overnight_orchestrator`)
- 0 registry rows

If a future extension DOES need a net-additional surface or service, the
charter demands paired retirement — an M5/M2-visible row must be retired in the
same PR.

## Sunset

The orchestrator's "success shape" is **zero auto-fix dispatches** — it means
the platform is already green enough that no signal needed one. Zero-dispatch
alone is NOT a retirement signal.

**Retirement candidate** = BOTH of the following for a **rolling 30-day window**:

1. Zero auto-fix dispatches in the 30-day window (across all
   `auto_fix_eligible` classes).
2. Non-zero `needs-operator` residue in the same window for the auto-fix-eligible
   `check_id` classes — i.e. the classes we WOULD have auto-fixed were still
   firing, but were routed to the operator instead (a signal that the auto-fix
   path is either broken or the classifier is too conservative to ever fire).

Neither condition alone is a retirement signal:

- Zero dispatches + zero needs-operator residue = SUCCESS, keep the machine.
- Non-zero dispatches + non-zero needs-operator residue = HEALTHY, keep the machine.
- Zero dispatches + zero eligible signals fired at all = quiet platform,
  ALSO keep the machine (the observability is still cheap).

Retirement steps (when the two-condition test flips):

1. File an operator-blocked ROADMAP item citing the 30-day window's evidence
   (dispatch count, residue count, `check_id` breakdown).
2. Operator decides: retire vs. adjust the classifier / adapters. Retirement
   removes the sidecar hook, the wire-in module, the wrappers, and this doc.
3. If retired, the daily-health check that used to escalate to the
   orchestrator instead escalates directly to the Slack alerts channel (i.e. the
   pre-orchestrator baseline).

The Sunset criterion binds parent-plan §Acceptance criteria's Sunset
reference — a future audit can point here for the retirement rule.

## References

- **Parent plan** (authority for §Objective, §Frozen decisions, §Plan steps,
  §Acceptance criteria): `docs/plans/overnight-detect-and-fix.md`
- **Live-mode child plan**: `docs/plans/overnight-live-mode-activation.md`
- **Sidecar hook**:
  `src/langgraph_sidecar/projects_daily_closeout.py::_invoke_overnight_orchestrator`
- **Public entrypoint**: `<workspace>/scripts/overnight_sidecar_wire_in.py::run_overnight_orchestrator_sidecar`
- **Adapters, classifier, gate, dispatcher, verifier, sign-off, digest**:
  `<workspace>/scripts/overnight_adapters.py`, `overnight_orchestrator.py`,
  `overnight_pre_action_gate.py`, `overnight_dispatch.py`,
  `overnight_post_action_verifier.py`, `overnight_orchestrator_signoff.py`,
  `overnight_orchestrator_morning_digest.py`
- **State queue** (schema owner): `<workspace>/scripts/overnight_state_queue.py`
- **Wrapper docs**: `<claude-config>/commands/overnight-orchestrator.md`,
  `<agent-skills>/skills/overnight-orchestrator/SKILL.md`
- **Parity regression test**:
  `<workspace>/scripts/test_overnight_wrapper_parity.py`
