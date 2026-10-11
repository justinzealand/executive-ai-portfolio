_Provenance: `observations.json` is the verbatim stdout of the review kernel's own observations module, run against the live packet store on 2026-10-10; this note explains it. Snapshot 2026-09-04, metrics regenerated 2026-10-10._
_Redacted: the command's filesystem paths are generalized here (the JSON itself contains no paths)._
_Content otherwise unchanged._

# Review-kernel observations — how to read `observations.json`

## Command

```bash
cd <agent-skills>/skills/review-kernel
python3 -m engine.observations \
  --packets-root <agent-skills>/state/review-kernel/packets \
  --window-days 400 --json
```

The observations module is the same code the daily health check `review_kernel.observations` runs; it folds every packet's append-only event log and reports the terminal outcomes. A 400-day window is deliberately wider than the kernel's lifetime, so the numbers are **cumulative since the 2026-07-23 cutover** (`v2-cutover-20260723T170500Z`), not a rolling sample.

## Fields

| Field | Meaning |
|---|---|
| `attempts` | Terminal pass attempts in the window — every review pass that reached one of the three outcomes (`content_verdict`, `operator_gate`, `harness_fault`). |
| `content_verdicts` | Attempts that ended in a real reviewer verdict (SHIP / ITERATE / BLOCKED / …) rather than a gate or a fault. |
| `harness_faults` | Attempts that ended because the review machinery failed (transport, quota, persistence, invalid reviewer output past the repair budget) — the failure class the v1 engine was replaced for. |
| `fault_rate` | `harness_faults / attempts`. |
| `pass_p50_s` | Median wall-clock seconds from attempt admission to terminal outcome. |
| `pass_p95_s` | 95th-percentile wall-clock seconds for the same. |
| `high_water` | Peak concurrent in-flight reviewer legs observed per provider (each provider's budget is 4). |
| `max_admission_wait_s` | Longest time any attempt waited in admission control for provider capacity before dispatch. |
| `quota_faults` | Harness faults specifically caused by a provider quota / rate limit. |
| `escalations` | Tier-L packets that were escalated to Tier S mid-review (an escalation supersedes the predecessor packet). |
| `budgets`, `evidence_*`, `recent_*`, `latency_evidence` | Configuration echo and the sub-window the latency percentiles were computed over; `latency_evidence: ok` means the sample was large enough to report. |

## Reading the 2026-10-10 snapshot

600 attempts, 591 content verdicts (98.5%), 9 harness faults (1.5%), p50 56.2 s, p95 114.3 s, zero quota faults, zero escalations, no attempt waited more than 100 ms for admission.

## The acceptance snapshot it extends

FINAL ship acceptance was declared on 2026-08-12 over the since-cutover window (30 days): **211 attempts / 206 content verdicts (97.6%) / p50 54.16 s / p95 122.29 s / harness-fault 2.37%**, against the frozen targets **p50 ≤ 180 s, p95 ≤ 480 s, ≥ 100 attempts, harness-fault ≤ 5%**. The cumulative figures above sit inside every one of those bounds; the fault rate has fallen since acceptance as the sample grew.
