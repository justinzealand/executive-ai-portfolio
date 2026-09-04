# Exhibit 04 — Governance as code

Five stdlib-only guards from a private workspace (snapshot 2026-09-04), paths generalized, behavior unchanged, each with its original tests.

| Module | One line |
|---|---|
| `registry_enforcement.py` | PreToolUse classifier checking outbound Bash writes, mutating MCP calls, and launchd create/start against three committed registries (ALLOWLIST, EXCEPTIONS, ALWAYS_ON). |
| `repo_guard.py` | Makes the primary workspace checkout read-only by construction; worktrees and sub-repos are never blocked. |
| `validate_consequential_write.py` | Pre-commit static scanner for statically-detectable "consequential write" shapes (bulk, delete, DDL, protected-path sinks, send/purchase). |
| `chip_claim_gate.py` | PreToolUse advisory on unhedged, uncited absolute historical claims in spawned-task prompts (immutable once spawned). |
| `spawn_task_gate.py` | Stops chip-spawned sessions from spawning grandchild chips (prompt-hash origin correlation plus an operator-owned flag). |

## Shared design rule

Fail-open on ambiguity, fail-loud on confirmed violation; the cleanup path is never blocked. Ambiguous input degrades to *allow*; a guard bug must never wedge a session or a commit.

## WARN → block promotion contract

`validate_consequential_write` and `chip_claim_gate` ship WARN-only. Promotion requires ≥20 adjudicated flags over ≥14 days with false-positive rate ≤5%. The gate never self-scores: only explicit operator adjudication marks a false positive; unadjudicated flags count toward neither numerator nor denominator.

## Running the tests

```
cd exhibits/04-governance-as-code && python3 -m pytest -q
```

Expected: 394 passed. `WORKSPACE_ROOT` is env-overridable; tests use tmp fixtures only.

## Excluded tests

- `test_registry_enforcement.py::test_load_signoff_lane_scoped_verifies_end_to_end` — imports the private `agent_dispatch_lane` module, not part of this exhibit.
