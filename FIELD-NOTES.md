# Field notes

Shorter lessons from operating the environment. Each was written down at the time as an operating rule with the incident that produced it; these are the ones that generalize beyond my environment. Dates are when the rule was recorded.

**The operator becomes the message bus.** (2026-06) When several parallel sessions each narrate a workspace-wide condition that none of them owns, the human ends up routing 22 paragraphs of prose that collapse to 7 real conditions, one of which is 64% of the volume and needs nothing. Prose contracts do not bind. The fix was a queue that deposits, dedupes, self-heals, and digests, so the human sees each real condition once with its owner and next step.

**Measure the false-positive rate behind a nudge before you let it write.** (2026-07) An auto-capture feature promoted from "nudge the human" to "write to the backlog" put 622 junk items into a roadmap across five commits in one night. The nudge had been fine. Ship the advisory form, measure its precision, then earn the write.

**Code binds; prose guides.** (2026-06) A rule that exists only in an instruction file loses to context pressure, reliably, across models. If a boundary matters, there must be a mechanism (a hook, a gate, a test) or an honest statement that it depends on a person.

**Dismissing needs evidence too.** (2026-07) "Benign", "already fixed", "can't reproduce", and "works as designed" are conclusions about real signals and require the same authoritative artifact as a fix: the log line, the diff against main, the live probe. A summary, a branch name, or a status field is a surface read and must be labeled as one.

**A detector's suppression rate is itself a finding.** (2026-07) When most of what a reviewer reports gets suppressed downstream, stop tuning the suppression and look at the reviewer. Emit the rate as a metric with a threshold.

**Observability is not control.** (2026-08) Detection outran remediation for months. A red verdict every morning, delivered reliably, is still a system that is asking the human to do the work. Pair every detective check with a structural preventer or an explicit reason there is none.

**No model in the alert path.** (2026-06) A model summarizing a health report once downgraded red to green. The decision to alert now runs in pure code from the collector's verdict; the model may add prose after the fact and cannot suppress the send.

**Silence from a monitor is not evidence of an outage; a fresh heartbeat is not evidence of health.** (2026-06) Both mistakes shipped. A ran-and-failed job writes a fresh heartbeat; a healthy job whose one startup line scrolled out of a fixed log window looks dead. Health gates must exercise the failure path, not pattern-match a window.

**Retire a control with the same evidence you introduced it with.** (2026-09) One sentence written under load became durable policy for six weeks and removed a working gate four days before its own acceptance. Governance changes need a goals layer to be checked against, in both directions.

**A spawned follow-up task is immutable; verify its premise before you send it.** (2026-08) A follow-up task whose prompt asserted a false historical fact instructed its executor to write that falsehood into the canonical instructions. A spawned task cannot be edited, so the only useful check is before it is sent: an unhedged absolute claim with no citation gets flagged.

**Autonomy is granted by consequence class and revoked by a named condition.** (2026-07) Every standing grant in the exceptions registry carries the condition that revokes it. An auto-merge path that cannot verify an independent approver degrades to opening a pull request and labeling it; it does not fall back to allow.

**Session ownership is fail-closed.** (2026-08) Work reported by a sibling session is context, not a next step, until it is explicitly transferred. Repo, directory, or topic proximity is not ownership. Most of the cross-session damage I saw came from an agent helpfully finishing someone else's work.
