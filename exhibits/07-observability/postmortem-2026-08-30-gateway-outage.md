_Provenance: original location `<workspace>/docs/incidents/2026-08-30-openclaw-gateway-launchd-reload-bootout.md` (private engineering workspace). Snapshot 2026-09-04._
_Redacted: filesystem paths generalized; hostnames and credential-store references generalized._
_Content otherwise unchanged._

# 2026-08-30 — OpenClaw gateway left unloaded by its own launchd restart handoff

**Impact:** the main OpenClaw gateway (port 18789) was down 18:05–18:12 EDT (~7 min).
Every OC cron, all Slack/Telegram delivery, and the deterministic daily health-alert
sender stopped with it. Nothing on the machine would have recovered it; the operator
noticed and ran `launchctl bootstrap` by hand.

**Installed version:** `openclaw@2026.7.1-2`
(`/opt/homebrew/lib/node_modules/openclaw/dist/`).

---

## 1. Mechanism

The outage is **not** the restart-deferral deadlock the log surface suggests. The
deferral is bounded and behaved correctly. The outage is a race in OpenClaw's
**launchd restart handoff**, which does `bootout` → `bootstrap` and gives up if the
bootstrap fails — leaving the job absent from the launchd domain, where
`KeepAlive: true` is meaningless.

### 1.1 The authoritative artifact

`<openclaw-config>/logs/gateway-restart.log` — written by the detached handoff script
itself (`renderPosixRestartLogSetup` → `resolveGatewayRestartLogPath`):

```
[2026-08-30T22:05:02Z] openclaw restart attempt source=launchd-handoff mode=reload target=gui/501/ai.openclaw.gateway waitPid=37241
Bootstrap failed: 5: Input/output error
Try re-running the command as root for richer errors.
[2026-08-30T22:05:06Z] openclaw restart failed source=launchd-handoff mode=reload status=37
```

(22:05Z = 18:05 EDT.) Compare the two *successful* restarts later the same evening,
which took a different mode and never booted out:

```
[2026-08-30T22:15:55Z] openclaw restart attempt source=launchd-handoff mode=kickstart ... 
[2026-08-30T22:16:15Z] openclaw restart done source=launchd-handoff mode=kickstart
[2026-08-30T23:08:10Z] openclaw restart attempt source=launchd-handoff mode=kickstart ...
[2026-08-30T23:08:31Z] openclaw restart done source=launchd-handoff mode=kickstart
```

`mode=reload` is the destructive path. `mode=kickstart` is not.

### 1.2 The code

`dist/launchd-*.js` → `restartLaunchAgent()`:

```js
if (isCurrentProcessLaunchdServiceLabel(label)) {
  const handoff = scheduleDetachedLaunchdRestartHandoff({
    env: serviceEnv,
    mode: await rewriteLaunchAgentPlistForRestart({...}) ? "reload" : "kickstart",
    waitForPid: process.pid
  });
  ...
}
```

Three facts follow from this:

**(a) The mode is chosen by whether the on-disk plist differs from OpenClaw's own
template.** `rewriteLaunchAgentPlistForRestart` rebuilds the plist from
`buildLaunchAgentPlist(...)` with `stderrPath: LAUNCH_AGENT_STDERR_PATH`, and
`const LAUNCH_AGENT_STDERR_PATH = "/dev/null"` is hardcoded. It returns `true`
(→ `mode: "reload"`) only when the rewritten content differs from what is on disk.
Our local hardening patch `06-gateway-stderr-log.sh` sets
`StandardErrorPath = <logs>/openclaw/gateway.err.log`. **So our own patch is
what selects the destructive path**, and it is re-armed every time
`apply-local-patches.sh --apply` runs. That is the self-perpetuating loop — though
not the one the log surface suggested.

**(b) `isCurrentProcessLaunchdServiceLabel` matches on inherited environment.**

```js
const currentLabels = [env.LAUNCH_JOB_LABEL, env.LAUNCH_JOB_NAME, env.XPC_SERVICE_NAME];
for (const currentLabel of currentLabels) if (currentLabel === label) return true;
```

`launchctl print gui/501/ai.openclaw.gateway` confirms the daemon runs with
`XPC_SERVICE_NAME => ai.openclaw.gateway`, and **every child process inherits it**.
So a short-lived `openclaw gateway restart` CLI spawned from inside the gateway (an
agent `exec`, a plugin, a cron) misidentifies itself as the daemon and passes its
*own* pid as `waitForPid`.

That is what happened here: `waitPid=37241` cleared in under a second (the handoff
logged its attempt at 22:05:02 and failed at 22:05:06), while the real daemon was
still alive at 18:05:19 — it was 14 seconds into a 300-second drain.

**(c) The handoff's wait budget is 3 seconds; the drain budget is 300 seconds.**

```js
const START_AFTER_EXIT_PRINT_RETRY_COUNT = 15;
const START_AFTER_EXIT_PRINT_RETRY_DELAY_SECONDS = .2;
```

`buildLaunchdRestartScript("reload")`:

```sh
launchctl enable "$service_target"
launchctl bootout "$service_target" >/dev/null 2>&1 || true
bootout_wait_count=15
while [ "$bootout_wait_count" -gt 0 ]; do
  if ! launchctl print "$service_target" >/dev/null 2>&1; then break; fi
  bootout_wait_count=$((bootout_wait_count - 1))
  sleep 0.2
done
if launchctl bootstrap "$domain" "$plist_path"; then status=0
else status=$?; launchctl kickstart -k "$service_target"; status=$?; fi
```

15 × 0.2s = **3 seconds** to wait for a service that is allowed to take **300 seconds**
(`DEFAULT_RESTART_DEFERRAL_TIMEOUT_MS = 3e5`) to drain. Two constants in the same
system that contradict each other.

There is also **no `launchctl load` fallback** — the deprecated-but-working recovery
for exactly the `Bootstrap failed: 5` case, which every one of our own local paths
(`restart-gateway-safe.sh`, `06-gateway-stderr-log.sh`) already uses.

### 1.3 Reconstructed timeline

| Time (EDT) | Event | Evidence |
|---|---|---|
| 18:01:42 | Prior restart completes; `marked 1 startup-orphaned main session(s) for restart recovery` | `gateway.err.log:11723` |
| 18:04:21 | `acp.dispatch.enabled` change requires restart; deferred behind 2 ops, 1 embedded run, 1 background task | `gateway.err.log:11728` |
| 18:04:21 | Blocker named: `title=[System] Your previous turn was interrupted by a gateway restart…` | `gateway.err.log:11729` |
| 18:05:02.x | A process holding the gateway's inherited `XPC_SERVICE_NAME` calls `restartLaunchAgent`; plist rewritten (mtime 18:05:02, `StandardErrorPath` → `/dev/null`); detached `mode=reload` handoff spawned with `waitPid=37241` | plist mtime; `gateway-restart.log` |
| 18:05:02.908 | Daemon receives SIGTERM → `received SIGTERM; restarting` → `draining 2 active task(s) and 1 active embedded run(s) before restart with timeout 300000ms` | `gateway.log` |
| ~18:05:03 | Handoff's `waitPid` clears (wrong pid); `bootout` issued; 3-second wait expires while the real daemon still holds the job | script constants |
| 18:05:06 | `bootstrap` → `Bootstrap failed: 5: Input/output error`; `kickstart -k` → 37; script exits. **Job now absent from `gui/501`.** | `gateway-restart.log` |
| 18:05:19 | Daemon still serving (`ws ⇄ res ✓ channels.status`) | `gateway.log` |
| ~18:10 | Drain times out, daemon closes and exits `0` expecting launchd to relaunch it. Nothing does — the job no longer exists. | `run-*.js handleRestartAfterServerClose` |
| 18:12:08 | Operator runs `launchctl bootstrap`; gateway returns | `gateway.log` |

### 1.4 Is the restart-recovery deadlock real?

**Real as a cycle, but bounded, and not the cause of the outage.**

- `main-session-restart-recovery-*.js` re-dispatches sessions interrupted by a restart
  as background task runs whose first message is
  `"[System] Your previous turn was interrupted by a gateway restart while OpenClaw was
  waiting on tool/model work…"`.
- Those runs are ordinary tasks. `isTaskRestartBlocker(task)` is
  `isActiveTaskRestartBlockerStatus(task.status) && !task.endedAt` — **no exclusion for
  recovery-originated runs**. So a restart-recovery task does block the next restart.
  The 18:01:42 recovery marking directly produced the 18:04:21 blocker.
- But the gate is bounded (300 s), the timeout path emits the restart anyway
  (`opts.hooks?.onTimeout?.(); emitPreparedGatewayRestart(..., opts.timeoutIntent)`),
  and a force-exit timer is armed at `drain + SHUTDOWN_TIMEOUT_MS`.

Its role in this incident is as an **amplifier**: it widened the daemon's exit window
from ~0 s to ~5 min, which guaranteed the handoff's 3-second bootout wait would expire.
With no blocker the daemon would have exited in ~200 ms and the same code path would
have succeeded.

### 1.5 Second-order damage still live

The 18:05:02 plist rewrite reverted `StandardErrorPath` to `/dev/null`, so
`<logs>/openclaw/gateway.err.log` has been dead since. That file is
load-bearing: `daily-health-check.py` treats it as a critical log
(`logs.tail_scan`, `LOAD_BEARING_STDERR_LABELS`) and `openclaw-watchdog` uses it as
its log-tail fallback cursor. `apply-local-patches.sh --check` currently reports
`NEEDS gateway-stderr-log`.

---

## 2. Local mitigation (landed)

`scripts/launchd_keepalive_rebootstrap_guard.py` + `com.justin.launchd-rebootstrap-guard`
(RunAtLoad + StartInterval=120s).

It watches `ai.openclaw.gateway` and `ai.openclaw.slack`, and when a KeepAlive
LaunchAgent is confirmed absent from `gui/<uid>` it runs the same two commands the
operator ran by hand: `launchctl bootstrap`, falling back to `launchctl load` on the
known-flaky `Bootstrap failed: 5`.

This is deliberately a **recovery**, not a prevention — it closes the "service left
unloaded, nothing retries" class regardless of which path unloaded it (OpenClaw's
handoff, `restart-gateway-safe.sh`'s `bootstrap_recovery`, `06-gateway-stderr-log.sh`'s
apply, or the post-`openclaw update` gap already documented in memory
`feedback_openclaw_update_requires_manual_bootstrap`).

Safety invariants, each pinned by a test in
`scripts/test_launchd_keepalive_rebootstrap_guard.py`:

1. The only launchctl subcommands it ever issues are `print`, `bootstrap`, `load`. A
   source-level test asserts no destructive subcommand literal (`bootout`, `unload`,
   `kickstart`, `stop`, `remove`, `disable`) appears in its code. It can add a missing
   service back; it can never take a healthy one away.
2. It never writes or edits a plist (it parses one read-only to confirm `KeepAlive`).
3. It acts only after 3 consecutive absent probes 2 s apart, so a legitimate in-flight
   bootout/bootstrap window (OpenClaw's own is at most 3 s) is never raced.
4. Fail-open on a missing / unparseable / non-KeepAlive plist or an unreadable
   `launchctl`.

Backstop for the guard itself: the daily-health `launchd.state` check, which grades an
unloaded KeepAlive service as fail (projects-workspace PR #2575).

### Applied 2026-08-30 20:04 (operator-approved)

`06-gateway-stderr-log.sh apply` re-asserted `StandardErrorPath`; gateway bounced and came
back healthy (`openclaw health --json` -> `ok: true`, pid 94920, `stderr path = …/gateway.err.log`,
log actively growing again). `apply-local-patches.sh --check` is clean. The re-bootstrap guard
was live underneath the bounce and logged `exit=0` across it.

**This restores the diagnostic surface but re-arms `mode=reload`** for the next restart — the
plist now differs from OpenClaw's template again, by design. That is the trigger the first
follow-up below removes.

## 2b. Trigger removal (landed 2026-08-30 22:27)

The guard above closes the *outcome*. This closes the *cause*: stderr capture moved out
of the plist and into the wrapper, so the plist can stay byte-identical to OpenClaw's
template and `mode=reload` becomes unreachable.

- `<openclaw-config>/bin/start-gateway.sh` redirects its own fd 2 to `gateway.err.log`
  (`exec 2>>`), which the exec'd node gateway inherits. Fail-open: an unopenable log
  leaves fd 2 alone and the gateway still boots. The path is env-overridable
  (`GATEWAY_ERR_LOG`) so tests never write the production log.
- `06-gateway-stderr-log.sh` is **inverted**. It no longer forces a real path into the
  plist; it asserts the plist is **byte-identical to the rendered OpenClaw template**
  AND the wrapper still carries the redirect. Byte comparison is load-bearing — upstream
  compares with string equality, so a semantically-correct plist written by PlistBuddy
  (alphabetised keys, tab indentation) still selects `mode=reload`. A field-level check
  passed that exact state during this change; the byte check caught it.
- `restart-gateway-safe.sh`'s `service_contract_ok()` now expects `/dev/null` in the
  plist and verifies the wrapper redirect instead.
- `daily-health-check.py` gains `WRAPPER_REDIRECTED_STDERR_LABELS` and two drift kinds:
  `launchd_stderr_wrapper_redirect_missing` (stderr going nowhere) and
  `launchd_stderr_plist_rearms_reload` (plist drifted back to naming a log).
  `launchd_crash_log` resolves the wrapper's log so `classify_stale_exit_freshness`
  signal 3 stays alive. `ai.openclaw.gateway` moved out of
  `LOAD_BEARING_STDERR_LABELS`; `ai.openclaw.slack` still uses the plist rule.

**Verification (live, in order):** plist rendered from the template → `plutil` clean →
gateway healthy → `stderr path = /dev/null` in `launchctl print` while `gateway.err.log`
kept growing (1,774,042 → 1,777,362 bytes) → patch check reports byte-identical →
`openclaw gateway restart` → **`runs = 2`**, i.e. the job never left the launchd domain,
which is only reachable when `rewriteLaunchAgentPlistForRestart` returns false and the
restart takes `kickstart -k`. That counter is the discriminator: the `mode=reload` branch
does bootout+bootstrap, which resets `runs` to 1.

Tests: `test_start_gateway.sh` 17 pass (3 new), `test_restart_gateway_safe.sh` 16 pass
(3 new), `test_daily_health_check.py` 1965 pass (11 new).

### Follow-ups not taken here

- **A staleness check on the guard's own `latest.json`** (it now emits `generated_at`).
- **`ai.openclaw.slack` parity.** The Slack gateway's plist diverges from OpenClaw's
  template the same way, so the identical hazard is live for it. Its wrapper
  (`<openclaw-slack-config>/bin/start-gateway.sh`) needs the same treatment; deliberately out
  of scope here because that repo's git tracking is unconfirmed and it carries its own
  `set -e` optional-credential-store gotcha. Tracked in ROADMAP.
- **Template drift after an OpenClaw upgrade.** The renderer in patch 06 mirrors
  `buildLaunchAgentPlist` from 2026.7.1-2. If upstream changes the template the patch
  will report NEEDS forever; the tell is `openclaw gateway restart` logging
  `mode=reload`, or `runs` resetting to 1 across a restart.

---

## 3. Upstream report (openclaw/openclaw)

**Filed 2026-08-30:** https://github.com/openclaw/openclaw/issues/133647 (scrubbed of local paths/hostnames; the four defects below are its body verbatim in substance).

**Title:** launchd `reload` restart handoff can leave the gateway permanently unloaded

**Version:** 2026.7.1-2, macOS 15 (Darwin 25.6.0), LaunchAgent install.

**Summary.** `restartLaunchAgent`'s in-process branch spawns a detached
`mode: "reload"` script that runs `launchctl bootout` and then `launchctl bootstrap`.
If the bootstrap fails, the script exits non-zero and the job is gone from the launchd
domain. `KeepAlive: true` cannot recover it, so the gateway stays down until a human
intervenes. Observed on 2026-08-30: gateway down ~7 minutes.

**Reproduction.**

1. Install the gateway as a LaunchAgent with `KeepAlive: true`.
2. Edit `<launch-agents>/ai.openclaw.gateway.plist` so it differs from
   `buildLaunchAgentPlist(...)` output — e.g. set `StandardErrorPath` to a real file
   rather than `/dev/null`. This makes `rewriteLaunchAgentPlistForRestart` return
   `true`, selecting `mode: "reload"`.
3. Start a long-running background task so the restart drain will not complete quickly
   (the default budget is `DEFAULT_RESTART_DEFERRAL_TIMEOUT_MS = 300000`).
4. From a process that inherits the gateway's environment (any child of the gateway —
   an agent `exec`, a plugin, a cron), run `openclaw gateway restart`.
5. Observe in `<openclaw-config>/logs/gateway-restart.log`:
   `openclaw restart failed source=launchd-handoff mode=reload status=…`, and
   `launchctl print gui/$(id -u)/ai.openclaw.gateway` → `Could not find service`.

**Four defects, in decreasing severity.**

1. **`bootout` without a guaranteed `bootstrap` is unsafe as written.** The reload
   script must not leave the domain in a worse state than it found it. Minimum fix: add
   a `launchctl load "$plist_path"` fallback after a failed `bootstrap` (deprecated but
   functional for user LaunchAgents, and the standard recovery for
   `Bootstrap failed: 5: Input/output error`), and retry `bootstrap` with backoff
   rather than exiting on the first failure. Better: verify with `launchctl print` and
   loop until the service is back or a hard deadline passes, logging loudly.
2. **`isCurrentProcessLaunchdServiceLabel` trusts an inherited `XPC_SERVICE_NAME`.**
   Any child process of the daemon inherits it, so a short-lived CLI concludes it *is*
   the daemon and passes its own pid as `waitForPid`. The handoff then stops waiting
   almost immediately and boots out a still-running service. Consider corroborating
   with the actual daemon pid (the gateway lock file / port owner) rather than
   environment alone, or resolving `waitForPid` from the service's real pid via
   `launchctl print`.
3. **The bootout wait budget (3 s) is two orders of magnitude below the restart drain
   budget (300 s).** `START_AFTER_EXIT_PRINT_RETRY_COUNT * START_AFTER_EXIT_PRINT_RETRY_DELAY_SECONDS`
   should be derived from, or at least bounded below by, `gateway.reload.deferralTimeoutMs`.
4. **Restart-recovery task runs block the restart that created them.**
   `main-session-restart-recovery` re-dispatches interrupted sessions as background task
   runs; `isTaskRestartBlocker` counts them, so
   `[System] Your previous turn was interrupted by a gateway restart…` appears as a
   restart blocker. It is bounded by the drain timeout so it only delays, but it
   reliably converts a fast restart into a 5-minute one — which is what turns defect 3
   into an outage. These runs should be excluded from the drain gate, or marked with a
   flag the gate skips.

**Workaround.** Keep `<launch-agents>/<label>.plist` byte-identical to
`buildLaunchAgentPlist` output so `rewriteLaunchAgentPlistForRestart` returns `false`
and the handoff uses `mode: "kickstart"`, which never boots out. Independently, run a
watchdog that re-bootstraps an unloaded KeepAlive LaunchAgent.
