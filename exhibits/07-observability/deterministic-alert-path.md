_Provenance: the module docstring of `<workspace>/scripts/health_alert_send.py` (verbatim), followed by a short rationale sourced from `<workspace>/docs/devlog/2026-06-25.md`. Snapshot 2026-09-04._
_Redacted: Slack channel name generalized; the module's channel-id and chat-id constants are not reproduced._
_Docstring unchanged; rationale paragraph authored from the devlog._

# The deterministic alert path

## `scripts/health_alert_send.py` — module docstring

```
Deterministic daily health-alert sender (RC3).

Fires the daily health alert SOLELY off the collector JSON's ``verdict`` field — no model in
the path, so an OC-cron model failure/timeout/NO_REPLY can never suppress a non-green alert.
Invoked by the launchd-driven ``trigger-health-check.sh`` (behind the ``HEALTH_DETERMINISTIC_
ALERTS`` flag) right after the collector writes the dated JSON; runs before, and independently
of, the prose-only OC review cron.

Channel policy (CG-8):
  - Slack alerts channel — EVERY non-green verdict (yellow + red + unknown), once per run_id.
  - Telegram — only incident transitions: red opens an episode, green recovers it, routine
    yellow is silent. ``episode_id`` makes a reopen-after-recovery a fresh open.

Dedup + send-then-record bias live in ``health_alert_lib``. ``--dry-run`` passes ``--dry-run``
to ``openclaw message send`` (no real send) and still records — used by the cutover verification.

Exit 0 always on a readable JSON (sends are best-effort + logged); exit 2 only if the JSON is
missing/unreadable (so the trigger can surface a real collector gap).
```

## Why the send decision is pure Python

Before 2026-06-25 the daily health alert was sent by an OpenClaw cron whose model read the collector's JSON, wrote a prose review, and decided whether to page. Two failure classes followed from putting a model in that path. First, a model failure, timeout, or silent `NO_REPLY` meant no alert at all — the day's non-green report simply went undelivered. Second, and worse, the model could read a non-green report and mis-grade it: the devlog for 2026-06-25 records that the prose path "mis-graded a non-green report as a GREEN downgrade," so the verdict-drift check that graded the cron's own prose summary had been trusting an unreliable proxy.

The fix (RC3, "LLM out of the alert SEND path") separated the two jobs. The collector writes a dated JSON with an authoritative `verdict`; a launchd trigger immediately runs this deterministic sender, which fires Slack on every non-green verdict and Telegram only on incident transitions, with schedule-slot dedup and an episode lifecycle in a local sqlite. The OpenClaw review cron was made send-incapable in the same change (`delivery=none`) — it can still append an LLM prose review to the day's note, but it can no longer decide whether the operator hears about a problem. Verdict-drift grading was repointed at the sender's own send record instead of the cron's prose. The result is the charter's Principle 5 applied to the one place it matters most: the decision to interrupt a human is deterministic; the model only adds commentary.
