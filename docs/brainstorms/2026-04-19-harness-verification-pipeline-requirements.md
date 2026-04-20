---
date: 2026-04-19
topic: harness-verification-pipeline
---

# Harness Verification Pipeline for TB2 Pass Rate Improvement

## Problem Frame

Pack currently passes 65% of Terminal Bench 2.0 tasks (34/52 agent-attempted). Analysis of 27 failures reveals three distinct modes:

1. **API hang (13 failures)** — OpenRouter SDK's 5s httpx timeout killed requests before they could complete. Fix deployed (client replacement with 300s timeout) but not yet validated at scale.
2. **Max tokens dump (4 failures)** — Model generates 65K tokens in one response instead of using tools. Fix deployed (16K cap) but not yet validated.
3. **Wrong solution (10 failures)** — Agent ran fully, produced output, but the solution was incorrect. **None of these tasks ran their own verification tests before declaring done.**

The wrong-solution failures are the primary target. The agent builds code and declares success without ever checking if it works — even though every TB2 task ships with verification tests.

## Requirements

**Layer 1: Auto-Verification at Harbor Wrapper Level**
- R1. After the agent completes, automatically execute `/tests/test.sh` in the sandbox container
- R2. If tests fail, inject the failure output (truncated to 2000 chars) as a new user message and re-invoke the agent
- R3. Allow up to 3 verification-fix cycles before accepting the result
- R4. If `/tests/test.sh` doesn't exist, skip auto-verification gracefully
- R5. Track verification attempts and outcomes in the trajectory metadata

**Layer 2: Planning Enforcement via Prompt**
- R6. The Harbor preamble must instruct the agent to output a numbered requirements checklist before writing any code
- R7. The checklist must reference exact details from the task: file paths, field names, output formats, edge cases
- R8. The preamble must instruct the agent to walk the checklist item-by-item after building, verifying each requirement is met

**Layer 3: Test-During-Build via Prompt**
- R9. The Harbor preamble must instruct the agent to run verification tests after the first draft, not wait until completion
- R10. The preamble must instruct: "If test files exist at /tests/, run them after your first draft and iterate on failures"

## Success Criteria

- TB2 pass rate on agent-attempted tasks increases from 65% to 75%+
- Zero wrong-solution failures where the agent never ran verification tests
- Auto-verification catches at least 50% of wrong solutions and allows self-correction
- No regression on currently-passing tasks

## Scope Boundaries

- This does NOT change the agent's core loop or middleware stack — changes are limited to the Harbor wrapper and prompt
- This does NOT require changes to TB2 task definitions
- Auto-verification uses the task's own tests, not Pack-generated tests
- Planning enforcement is via prompt guidance, not mechanical enforcement

## Key Decisions

- **Convention-based test discovery** — Always try `/tests/test.sh` rather than parsing task.toml. Simple, reliable, works for TB2. Prompt backup handles edge cases.
- **Wrapper-level verification, not agent-level** — The agent has proven it won't self-verify. The wrapper mechanically runs tests after the agent declares done. This is the blog's principle: "mechanical enforcement beats documentation."
- **3 verification cycles** — Enough to catch and fix most issues without burning the full timeout budget. Each cycle: run tests → feed errors → agent fixes → re-test.
- **Prompt layers are reinforcement, not primary mechanism** — Planning and test-during-build are prompt-level guidance. Auto-verification is the safety net that catches what the agent misses.

## Outstanding Questions

- **Resolve during planning:** How does auto-verification interact with the agent timeout? If 3 cycles each take 2-3 minutes, that's 6-9 minutes of the 15-minute budget. May need to adjust cycle count based on remaining timeout.
- **Resolve during planning:** Should verification errors be injected as user messages or system messages? User messages are more visible to the model but may confuse the conversation structure.
- **Defer to implementation:** Exact truncation strategy for test output (first N chars, last N chars, or error-focused extraction).
