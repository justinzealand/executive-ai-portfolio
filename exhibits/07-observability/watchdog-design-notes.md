_Provenance: excerpted from the `openclaw-watchdog` repo README (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized; knowledge-base product name generalized._
_Content otherwise excerpted — the pipeline block and the design-decision bullets are verbatim._

# openclaw-watchdog — design notes (excerpt)

OpenClaw plugin for real-time gateway observability, error detection, self-healing, and multi-channel alerting. Observe-only in Phase 1; Phase 1.5 adds cron auto-retry and investigation agents.

## Pipeline

Monitors the OpenClaw gateway across four detection paths — hooks, diagnostics, cron poll, and log tail — and routes every detected event through a central `_emit()` pipeline:

```
Error detected
  ↓
_emit(record)
  ↓  dedup (same fingerprint within 5min → suppress)
Write to audit JSONL  (<openclaw-config>/watchdog/audit.jsonl)
  ↓
Pattern tracker  (24h rolling window per cron job / trigger)
  ↓
AlertManager  → T2: desktop notification (cooldown 15min)
              → T3: Slack + Telegram (CRITICAL), cooldown 30min
  ↓
Self-heal (async)       → retry failed cron job (circuit breaker: 5/hr)
Investigation (async)   → spawn OC agent, findings → triage database
```

## Detection paths

| Path | Signal source | What it catches |
|---|---|---|
| Gateway hooks | `session_start/end`, `agent_end`, `after_tool_call`, `before_compaction`, `gateway_start/stop`, `message_sent` | Agent failures, tool failures/cascades/slow calls, context overflow, message send failures |
| OC diagnostics | `onDiagnosticEvent()` | Stuck sessions, tool loops, queue pressure, webhook errors |
| Cron health poll | sqlite `cron_jobs` table every 30s (read-only) | Failed / repeated-failure / overdue cron jobs |
| Log tail fallback | `gateway.err.log` streaming cursor | Plugin load failures, provider connection errors |

## Key runtime defaults

| Key | Default | Notes |
|---|---|---|
| `dedupWindowSeconds` | 300 | Suppress identical fingerprint within window |
| `selfHealMaxActionsPerHour` | 5 | Circuit breaker |
| `cronRetryMaxConsecutive` | 1 | One retry per failure, no pile-on |
| `investigationMaxPerHour` | 3 | Investigation budget |
| `investigationTimeoutMs` | 120 000 | 2-min timeout per investigation agent |

## Key design decisions

- **Hooks + diagnostics first, logs last** — structured OC events preferred over log parsing
- **`session.stuck` diagnostic is primary** — OC detects this natively; don't reinvent it
- **Compaction = warning-first** — single compaction is normal; escalates only after 3+
- **Chain-reaction prevention** — every automated action has a circuit breaker and global budget
- **`notifier_failed` is isolated** — alert failures never re-enter the alert pipeline
- **Investigation agents are read-only** — diagnose and recommend; never auto-apply fixes
- **Cannot detect own load failure** — requires external supervisor (`watchdog-supervisor.sh`) by necessity

## Infrastructure services

- `watchdog-supervisor.sh` via launchd every 15 min — external watchdog for the watchdog itself
- Dashboard launchd plist — keeps the local dashboard always alive
