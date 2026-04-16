---
title: "refactor: Harden Harbor retry and capture partial trajectory on failure"
type: refactor
status: active
date: 2026-04-15
origin: docs/ideation/2026-04-15-post-upstream-sync-ideation.md
---

# Harden Harbor Retry and Capture Partial Trajectory on Failure

## Overview

Pack's Harbor wrapper already retries `ainvoke()` on transient errors, but the implementation has three gaps: it has zero test coverage, it classifies "server disconnect" via fragile string-matching on exception text, and it silently loses the entire trajectory when all retries fail — which is the failure mode covering 36% of observed Harbor crashes. This plan hardens the existing retry, adds structured error classification with test coverage, and captures a partial trajectory on terminal failure so post-mortem tooling (`skills/langsmith-trace-analyzer/`) has something to analyze even when a run dies with no final message.

## Problem Frame

From `docs/plans/tb2-harbor-run-feedback.md` and the current ideation doc: ~36% of Harbor failures are OpenRouter "Server disconnected without sending a response" crashes. The wrapper's existing `_invoke_with_retry` catches these via *string-match* on the exception message (`"server disconnected"`, `"connection reset"`, `"eof"`, `"broken pipe"`), which is brittle and untested. When all three retry attempts fail, the exception propagates out of `run()` and `_save_trajectory()` is never called — so the job directory gets no trajectory JSON and LangSmith gets only an error. We lose the accumulated messages, the tool calls the agent made, and any partial progress.

The fix is not "retry harder" — that's already there. The fix is (a) replace string-matching with a small, tested classifier, (b) capture what we have when the final attempt fails, (c) annotate the LangSmith run so retries are visible for analysis, and (d) make the whole thing regression-resistant with proper unit tests.

**Not in scope:** Checkpointer-aware resume (restart a partially-completed run from the last checkpoint instead of replaying). That's more invasive and requires auditing every Pack middleware for idempotency — deferred. The 36% failure mode is "stream dies before first tool call," so idempotent retry from scratch covers the dominant case.

## Requirements Trace

- **R1** — The retry function must have direct unit test coverage that exercises: transient-error retry success, terminal-failure propagation, non-retryable-error pass-through, backoff timing.
- **R2** — Error classification must replace string-matching with a structured predicate that is testable in isolation and extensible when new transient error types are discovered.
- **R3** — When all retry attempts fail in `DeepAgentsWrapper.run()`, a partial trajectory must be persisted to the job directory containing whatever messages were accumulated (if any) plus a structured failure marker.
- **R4** — LangSmith traces must record retry attempt count and terminal failure reason in metadata so the trace analyzer can distinguish "succeeded on first try" from "succeeded on retry 2" from "died after 3 retries."
- **R5** — No regression in the happy path — the existing retry behavior must continue to work identically when all attempts succeed.

## Scope Boundaries

- **In scope:** Retry function hardening, error classification module, partial trajectory capture on terminal failure, LangSmith retry annotation, test coverage.
- **Not in scope:** Checkpointer-aware resume (would require middleware idempotency audit).
- **Not in scope:** Retry budget across the whole TB2 run (only per-invocation retry).
- **Not in scope:** Provider-specific retry policies (OpenAI Responses API, Anthropic prompt-cache invalidation on retry).

### Deferred to Separate Tasks

- Checkpointer-resume retry: deferred to a follow-up plan once a middleware-idempotency audit is done.
- Auto-retry metric export to a dashboard: depends on ideation #3 (TB2 regression benchmark gate).

## Context & Research

### Relevant Code and Patterns

- `libs/evals/deepagents_harbor/deepagents_wrapper.py:43-109` — existing retry constants and `_invoke_with_retry` implementation. Two-codepath design (type-based for `_RETRYABLE_ERRORS`, string-match for disconnect strings) is what this plan consolidates.
- `libs/evals/deepagents_harbor/deepagents_wrapper.py:304-426` — `run()` method. The retry is invoked at two call sites (`run()` lines 410 and 420, branched on `LANGSMITH_EXPERIMENT` presence). Both sites must be covered.
- `libs/evals/deepagents_harbor/deepagents_wrapper.py:428-552` — `_save_trajectory()`. The signature reads `result["messages"]`; partial capture needs to accept `result | None` and emit a failure marker when `None`.
- `libs/evals/tests/unit_tests/test_harbor_backend.py` — existing test pattern with `_FakeHarborEnvironment` and `_FakeExecResult`. The new retry tests should follow the same fake-injection style rather than mocking `asyncio.sleep` globally.
- `harbor.models.trajectories.{Trajectory, Step, FinalMetrics}` — existing trajectory schema. A failure marker should fit into the existing `Trajectory.metadata.extra` or a dedicated `terminated_reason` field if one exists; investigate in Unit 3.

### Institutional Learnings

- `docs/plans/2026-04-06-001-feat-iterative-task-performance-plan.md` — explicitly prescribed wrapper-level retry for transient connection errors (2s/4s/8s exponential backoff, 3 attempts). That's what exists today. The follow-up work (this plan) closes the observability/classification gaps that plan left open.
- `docs/plans/tb2-harbor-run-feedback.md` — LangSmith analysis: 4 of 11 completed failures were OpenRouter disconnects with no trajectory captured. Partial trajectory capture directly addresses this.
- `docs/ideation/2026-04-15-post-upstream-sync-ideation.md` — this plan's origin idea (#1). The ideation assumed retry was unimplemented; plan scope refocuses on the gaps that *are* real.

### External References

Not needed for this work — the retry pattern is standard exponential backoff; classification follows established Python practice; LangSmith's `run_tree.metadata` extension is documented in its SDK.

## Key Technical Decisions

- **Error classification via a typed predicate, not string-matching.** A small `_is_transient_error(exc: BaseException) -> bool` function that (a) checks `isinstance(exc, _RETRYABLE_ERROR_TYPES)`, (b) walks the exception chain (`__cause__` / `__context__`) looking for retryable types, (c) falls back to a narrow string heuristic *only* for `RuntimeError`/`Exception` subtypes where the provider SDK is known to raise a generic exception with a disconnect message. This keeps the string-match narrowly scoped and individually testable. **Why:** string-match on `str(exc).lower()` matches any nested "eof" substring and has no safety rail.
- **Partial trajectory capture runs in a `try/finally` around `_invoke_with_retry`, not inside the retry function itself.** The retry function stays small and single-purpose. The wrapper owns "what to persist when it fails." **Why:** retry is a generic primitive; trajectory capture is wrapper-specific.
- **Persist partial trajectories as `trajectory.json` with a top-level `status: "failed"` and a `failure: { reason, exception_type, attempts }` block**, not as a separate file. **Why:** downstream tooling (`skills/langsmith-trace-analyzer/`) already reads `trajectory.json`; adding a sibling file would require updating every consumer.
- **LangSmith annotation uses `run_tree.metadata.update({"retry_attempts": N, "terminated": bool, ...})` before `run_tree.end()`** on both branches (with and without `LANGSMITH_EXPERIMENT`). **Why:** metadata is queryable in LangSmith UI; tags are not.
- **Keep the public call shape of `_invoke_with_retry` unchanged** — same args, same return. **Why:** call sites at lines 410/420 don't need to be rewritten; the function just becomes better-instrumented and better-tested.

## Open Questions

### Resolved During Planning

- **Do we add a retry attempt limit in total seconds, not just count?** No — 3 attempts at 2/4/8s is already bounded at ~14s which is acceptable. Adding a wall-clock bound adds a second knob without material benefit at current attempt counts.
- **Should retry be opt-in via env var?** No — it already runs unconditionally in Harbor. Keep that. If we need a bypass for debugging, `PACK_HARBOR_RETRY_ATTEMPTS=1` can be added later (implementation note, not a new requirement).
- **Do we add jitter?** Yes, small uniform `[0, 0.5s)` jitter per attempt. Cheap, prevents synchronized retry bursts when a whole TB2 batch hits a provider outage simultaneously.

### Deferred to Implementation

- **Exact metadata key names for LangSmith annotation** — `retry_attempts` vs `pack_retry_attempts` vs `harbor.retry_attempts`. The implementer should check existing metadata keys in the Harbor wrapper and stay consistent.
- **Whether `Trajectory` has a native `status` field or we use `extra`** — depends on the `harbor.models.trajectories.Trajectory` schema version pinned; implementer checks first, uses native field if present, `extra` if not.
- **Whether `_is_transient_error` lives in `deepagents_wrapper.py` or a new `retry.py` module** — decide during Unit 2 based on whether Unit 4's LangSmith annotation pulls it toward needing shared helpers. Default: inline until a second caller appears.

## Implementation Units

- [ ] **Unit 1: Add regression-proof unit tests for the existing retry**

**Goal:** Lock the current observable retry behavior under test before refactoring it. Prevents "subtly broke retry timing while hardening classification."

**Requirements:** R1, R5

**Dependencies:** None

**Files:**
- Create: `libs/evals/tests/unit_tests/test_harbor_retry.py`

**Approach:**
- Inject a fake agent with a scripted `ainvoke` that raises a sequence of errors then returns a result on a specified attempt. Drive the existing `_invoke_with_retry` against it.
- Monkeypatch `asyncio.sleep` with a no-op async function to avoid real sleep delays in tests — but assert that the real function was *called* with the expected durations (2s, 4s, 8s at the three attempts).
- Do not mock the classifier yet — tests target end-to-end retry behavior, not internal predicates. That's Unit 2's job.

**Execution note:** Test-first. Any change to retry behavior in Units 2-4 must keep these tests green.

**Patterns to follow:**
- `libs/evals/tests/unit_tests/test_harbor_backend.py` — fake-injection fixture style.
- `tests/unit_tests/test_pack_commands.py` in `libs/cli/` (note: some of these are already known-broken for unrelated reasons; mirror the `AsyncMock` pattern only).

**Test scenarios:**
- **Happy path:** Agent succeeds on first attempt → `_invoke_with_retry` returns the result, `asyncio.sleep` is never called.
- **Happy path:** Agent fails with `ConnectionError` on attempt 1, succeeds on attempt 2 → returns attempt-2 result, `sleep(2.0)` called once.
- **Happy path:** Agent fails on attempts 1 and 2 with `TimeoutError`, succeeds on attempt 3 → `sleep` called with `2.0` then `4.0`.
- **Error path:** Agent fails on all 3 attempts with `ConnectionError` → last exception is re-raised, `sleep` called twice (before attempts 2 and 3, not before attempt 1 or after attempt 3).
- **Error path:** Agent raises a non-retryable `ValueError` on attempt 1 → re-raised immediately, `sleep` never called, no further attempts.
- **Error path:** Agent raises generic `RuntimeError` with `"Server disconnected"` in message on attempt 1, succeeds on attempt 2 → current string-match path covered; retry fires; test guards against regression when Unit 2 replaces the string-match with a classifier.
- **Edge case:** `max_attempts=1` → zero retries; exception re-raised on first failure.

**Verification:**
- All scenarios pass under `uv run pytest libs/evals/tests/unit_tests/test_harbor_retry.py`.
- Running full `libs/evals` unit suite remains green.

---

- [ ] **Unit 2: Replace string-matching with a structured `_is_transient_error` classifier**

**Goal:** Consolidate the two-codepath retry (type-check + string-match) into one predicate that's testable in isolation and extensible.

**Requirements:** R2, R5

**Dependencies:** Unit 1 must be green first.

**Files:**
- Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py`
- Modify: `libs/evals/tests/unit_tests/test_harbor_retry.py`

**Approach:**
- Extract a module-private `_is_transient_error(exc: BaseException) -> bool` that returns True when the exception (or any `__cause__` / `__context__` in its chain) is an instance of the retryable type tuple OR is a generic `Exception`/`RuntimeError` whose message matches one of the narrow disconnect substrings.
- Rewrite `_invoke_with_retry`'s loop to use a single `try/except Exception as exc: if _is_transient_error(exc): ...` branch instead of the current two `except` blocks.
- Preserve the exact public behavior Unit 1 pinned: same backoff timing, same attempt count, same raise-from-last behavior.
- Add small jitter: `delay = base * 2**(attempt-1) + random.uniform(0, 0.5)`.

**Technical design:** *(directional guidance, not implementation specification)*

```
_is_transient_error(exc):
    # Direct type match
    if isinstance(exc, _RETRYABLE_ERROR_TYPES): return True
    # Walk cause chain (for wrapped errors from langchain / httpx)
    cur = exc.__cause__ or exc.__context__
    while cur:
        if isinstance(cur, _RETRYABLE_ERROR_TYPES): return True
        cur = cur.__cause__ or cur.__context__
    # Narrow string fallback only for generic exception types
    if type(exc) in (Exception, RuntimeError):
        return any(marker in str(exc).lower() for marker in _DISCONNECT_MARKERS)
    return False
```

**Patterns to follow:**
- Standard Python exception-chain walking. No external dependency.

**Test scenarios:**
- **Happy path:** Direct `ConnectionError`, `TimeoutError`, `OSError` instances → classifier returns True.
- **Happy path:** `ValueError` → False.
- **Edge case:** `RuntimeError("connection reset by peer")` → True (string fallback hits).
- **Edge case:** `ConnectionError` wrapped as `__cause__` of a generic `RuntimeError("something bad")` → True (chain walk catches it).
- **Edge case:** `ValueError("server disconnected")` — a non-generic type with a disconnect-looking message → False (string fallback is gated on type).
- **Edge case:** Exception with cyclic `__cause__` chain (pathological) → classifier terminates without infinite loop.

**Verification:**
- Unit 1's existing tests all remain green (pinning behavior preservation).
- New classifier tests pass.
- `grep` confirms no remaining `str(exc).lower()` or `_RETRYABLE_ERRORS` tuple references outside `_is_transient_error`.

---

- [ ] **Unit 3: Capture partial trajectory when all retries fail**

**Goal:** Ensure every TB2 trial produces a `trajectory.json` in its job directory, even when the agent invocation dies — so the trace analyzer has data to cluster.

**Requirements:** R3

**Dependencies:** Unit 1 green (so retry is under test before we wrap it).

**Files:**
- Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py`
- Create: `libs/evals/tests/unit_tests/test_harbor_partial_trajectory.py`

**Approach:**
- In `run()`, wrap the two `_invoke_with_retry` call sites in a `try` that, on any exception, constructs a partial trajectory from whatever `last_result` we captured (if any) and from the original `instruction` alone if not. Persist it via a refactored `_save_trajectory()` that accepts `result: dict | None` and a `failure: FailureInfo | None` parameter.
- `FailureInfo` is a small local dataclass with `reason: str`, `exception_type: str`, `attempts: int`, `final_exception_repr: str` (truncated to ~2KB to avoid blowing up the JSON).
- After persisting, re-raise the original exception so Harbor still sees the trial as failed and records the right outcome at its layer. We're adding observability, not swallowing errors.
- If `LANGSMITH_EXPERIMENT` was set, call `run_tree.end(error=str(exc))` inside the `except` block so LangSmith marks the run as errored.

**Technical design:** *(directional guidance)*

```
async def run(...):
    ...
    last_result = None
    failure = None
    try:
        if langsmith_experiment_name:
            with trace(...) as run_tree:
                try:
                    last_result = await _invoke_with_retry(...)
                except Exception as exc:
                    failure = _build_failure_info(exc, attempts=...)
                    run_tree.end(error=str(exc))
                    raise
                else:
                    run_tree.end(outputs={...})
        else:
            try:
                last_result = await _invoke_with_retry(...)
            except Exception as exc:
                failure = _build_failure_info(exc, attempts=...)
                raise
    finally:
        # Always persist whatever we have, including on success
        self._save_trajectory(environment, instruction, last_result, infra_meta, failure=failure)
```

**Patterns to follow:**
- `docs/plans/2026-04-04-002-feat-verify-retry-loop-plan.md` — similar try/finally structure around agent invocation for verification retry (completed plan, precedent for the pattern).
- Existing `_save_trajectory` message-loop code stays untouched; only its signature grows.

**Test scenarios:**
- **Happy path:** `_invoke_with_retry` returns normally → `_save_trajectory` called with `result` non-None, `failure=None`. Behavior matches current implementation.
- **Error path:** `_invoke_with_retry` raises after 3 attempts → `_save_trajectory` called with `result=None`, `failure.reason="retry_exhausted"`, `failure.attempts=3`, `failure.exception_type="ConnectionError"`. Exception re-raised after persistence.
- **Error path:** `_invoke_with_retry` raises non-retryable `ValueError` on first attempt → `_save_trajectory` called with `result=None`, `failure.attempts=1`, `failure.reason="non_retryable"`. Exception re-raised.
- **Integration:** With `LANGSMITH_EXPERIMENT` set, terminal failure causes `run_tree.end(error=...)` to be called exactly once before re-raise. (Assert via a fake trace context manager.)
- **Edge case:** `_save_trajectory` itself raises while building the failure trajectory → original exception from the invoke path must still propagate (the persistence failure should not mask the root cause). Use `logger.exception` inside the `finally` and swallow the secondary exception.
- **Edge case:** Partial trajectory with zero messages → output JSON has `status="failed"`, `steps=[user_instruction_step]`, and the failure block. `trajectory-analyzer` skill should still parse it.

**Verification:**
- Partial-trajectory tests pass.
- Existing `test_harbor_backend.py` and Unit 1 / Unit 2 tests still green.
- Manually inspect the output of a simulated failure: `uv run python -c "..."` smoke check produces a `trajectory.json` with the failure block well-formed.

---

- [ ] **Unit 4: Annotate LangSmith traces with retry metadata**

**Goal:** Make "did this run retry?" a queryable dimension in LangSmith — so the trace analyzer can cluster by "succeeded on retry N" and distinguish first-try success from recovery.

**Requirements:** R4

**Dependencies:** Units 2 and 3.

**Files:**
- Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py`

**Approach:**
- `_invoke_with_retry` returns a small metrics tuple alongside the result: `(result, attempts_used: int)`. Update the two call sites to unpack both and pass `attempts_used` into the LangSmith metadata before `run_tree.end()`.
- When `LANGSMITH_EXPERIMENT` is not set, add `attempts_used` to the existing `config["metadata"]` dict so it flows into LangSmith via the standard `RunnableConfig` path.
- Metadata keys: `retry_attempts` (int), `retry_terminated` (bool, true when all attempts failed), `retry_final_exception_type` (str, only set when terminated).

**Patterns to follow:**
- Existing `metadata = {...}` construction in `run()` (lines 368-381) — just extend the dict.
- LangSmith's `run_tree.metadata.update(...)` is documented as additive; safe to call.

**Test scenarios:**
- **Happy path:** First-attempt success → `attempts_used=1` appears in metadata. `retry_terminated=False`.
- **Happy path:** Attempt 2 success after `ConnectionError` → `attempts_used=2`. Metadata reaches the trace.
- **Error path:** All attempts fail → `attempts_used=3`, `retry_terminated=True`, `retry_final_exception_type="ConnectionError"` in metadata (and then the exception re-raises per Unit 3's contract).
- **Integration:** With `LANGSMITH_EXPERIMENT` set, `run_tree.metadata` receives the annotations before `.end()`. Without it, `config["metadata"]` carries them. Assert both paths via fake trace + fake Runnable config.

**Verification:**
- New tests pass; Unit 1 / 2 / 3 tests remain green.
- A live Harbor smoke task against a real LangSmith project shows `retry_attempts` in the run metadata in the UI.

## System-Wide Impact

- **Interaction graph:** `_invoke_with_retry` is called at exactly two sites, both in `DeepAgentsWrapper.run()`. `_save_trajectory` is called at one site. No other callers. Blast radius is contained to the Harbor wrapper.
- **Error propagation:** Exceptions still propagate to Harbor's trial-runner layer. This plan adds persistence and observability on the way out but does not swallow errors.
- **State lifecycle risks:** Partial trajectory capture writes to the same file path the success path writes to. A run that retries and ultimately succeeds overwrites nothing; a run that dies writes a `status="failed"` variant. No cache or cleanup concerns.
- **API surface parity:** `_save_trajectory`'s signature grows a new keyword arg (`failure=None`). Since it's private (leading underscore) and only called from one site, no external consumers break.
- **Integration coverage:** Unit 3's integration scenario (LangSmith trace + retry + terminal failure) is the only multi-layer interaction; covered explicitly.
- **Unchanged invariants:** Success-path `_save_trajectory` output format is byte-identical to today's when `failure=None`. The `trajectory.json` schema only gains fields; nothing is renamed or removed. Existing downstream consumers (`skills/langsmith-trace-analyzer/`, `.github/scripts/aggregate_evals.py`) continue to read the file without modification.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Refactor breaks the exact backoff timing that's tuned for OpenRouter's recovery window | Unit 1 tests pin the timing explicitly before Unit 2 refactors; any deviation fails a test. |
| Exception-chain walking hits a cyclic chain and infinite-loops | Unit 2's cyclic-chain test scenario; implementer must cap the walk depth (e.g., 10 frames). |
| `trajectory.json` schema extension breaks downstream tooling | Only additive fields. `status="failed"` is new; consumers should ignore unknown fields (verify `aggregate_evals.py` and the trace analyzer before merge). |
| Partial-trajectory code runs inside `finally` and masks the original exception if it itself raises | Wrap `_save_trajectory` inside the `finally` with its own `try/except` that logs and swallows, per Unit 3's edge-case test. |
| Adding jitter changes observed retry timing in tests that pin exact sleep durations | Unit 1 tests assert `sleep` was called with `>= base * 2**(n-1)` rather than exact equality; jitter fits under this assertion. |

## Documentation / Operational Notes

- Add a one-paragraph note to `libs/evals/deepagents_harbor/README.md` (or create the section if it doesn't exist) explaining the retry behavior and the new `trajectory.json` `status` field, so analyzer authors know the contract.
- No operational or monitoring changes needed — LangSmith already captures the runs; this plan just enriches the metadata.

## Sources & References

- **Origin document:** [docs/ideation/2026-04-15-post-upstream-sync-ideation.md](docs/ideation/2026-04-15-post-upstream-sync-ideation.md)
- Related code: `libs/evals/deepagents_harbor/deepagents_wrapper.py:43-109, 304-552`
- Related plans: [docs/plans/2026-04-06-001-feat-iterative-task-performance-plan.md](docs/plans/2026-04-06-001-feat-iterative-task-performance-plan.md) (completed — prescribed the original retry), [docs/plans/tb2-harbor-run-feedback.md](docs/plans/tb2-harbor-run-feedback.md) (LangSmith disconnect analysis)
