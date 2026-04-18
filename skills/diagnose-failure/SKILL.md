---
name: diagnose-failure
description: Structured root-cause analysis with hypothesis tracking. Force orthogonal approach on repeated failures
triggers: Verify fails 2+ times on same issue
allowed-tools: [read_file, shell, write_file]
---

# Diagnose Failure

Perform structured root-cause analysis when iterative fixes are not converging. Track hypotheses explicitly, test them methodically, and force orthogonal approaches when repeated attempts fail on the same symptom.

## Purpose

Agents fall into fix-retry loops where they apply superficially different patches to the same root cause. Diagnose-failure breaks this loop by requiring explicit hypothesis formation, evidence gathering, and -- critically -- forcing a fundamentally different approach after two failed hypotheses on the same symptom. The skill produces a diagnosis artifact that records the investigation for future reference.

## Input / Output

**Input:**
- `symptom`: Description of the failure (error message, test output, behavior)
- `history`: Prior fix attempts and their results (from verify-and-iterate log)
- `context`: Relevant code paths, config, environment details

**Output:**
- Diagnosis artifact with:
  - Symptom classification
  - Hypotheses tested (with evidence for/against each)
  - Root cause identified (or "unresolved" with recommended next steps)
  - Fix applied and verification result
- Escalation flag if root cause cannot be determined

## Interaction with Other Skills

- **verify-and-iterate**: Escalates here after 2+ failures on the same issue
- **bootstrap-repo**: Provides context about the codebase to inform diagnosis
- **plan-and-execute**: A diagnosis may require revising the current plan

## Example Invocation

```
diagnose-failure:
  symptom: "TypeError: Cannot read property 'id' of undefined at UserService.getProfile"
  history:
    - attempt: "Added null check before access"
      result: "Same error on different code path"
    - attempt: "Added default user object"
      result: "Error moved to downstream serialization"
  context: { file: "src/services/user.ts", test: "npm test -- --grep UserService" }
```
