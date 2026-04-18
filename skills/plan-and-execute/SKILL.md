---
name: plan-and-execute
description: Create execution plan artifact, track progress, checkpoint state
triggers: Complex multi-step task requiring coordination
allowed-tools: [read_file, write_file, shell, edit_file]
---

# Plan and Execute

Decompose a complex task into an ordered execution plan, track progress through each step, and checkpoint state so work can be resumed or audited.

## Purpose

Multi-step tasks fail when agents lose track of what has been done, what remains, and what depends on what. Plan-and-execute creates a persistent plan artifact (markdown or structured file) that serves as both a roadmap and a progress tracker. Each step has a clear definition of done, and the plan is updated in place as work proceeds.

## Input / Output

**Input:**
- `objective`: High-level description of the goal
- `context`: Repo context (ideally from `bootstrap-repo`)
- `constraints`: Time, scope, or architectural constraints

**Output:**
- Plan artifact file with numbered steps, dependencies, and status markers
- Per-step checkpoints recording what was done and any artifacts produced
- Final summary with completed/skipped/failed step counts

## Interaction with Other Skills

- **bootstrap-repo**: Provides repo context to inform plan structure
- **verify-and-iterate**: Invoked as checkpoint steps within the plan
- **review-diff**: Called before any commit step in the plan
- **diagnose-failure**: Escalation target when a plan step fails repeatedly

## Example Invocation

```
plan-and-execute:
  objective: "Migrate authentication from session-based to JWT"
  context: { framework: "Express", test_cmd: "npm test" }
  constraints: "Do not break existing /api/v1 endpoints"
```
