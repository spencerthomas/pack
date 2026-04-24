# Coding-task context pack

This is the generic pack applied whenever a classified coding task runs
and no more specific pack matches. It names the default behaviors the
agent should follow in the absence of domain-specific rules.

## What this pack covers

- Any language, any repo layout, any phase.
- Intended as the *floor* — specialized packs override these rules
  when they load alongside.

## What this pack does not cover

- Business logic (domain packs own that).
- Deployment, CI/CD configuration, or infra — handled by per-repo
  runbooks, not by the agent.
- Anything the `.harness/` policy layer already enforces (scope,
  max file changes, approval). Those are hard constraints the agent
  cannot renegotiate.

## Entry points

When the task is classified and this pack is selected, the harness
passes the pack's ``rules.md`` content into the system prompt as a
cacheable static section. The rules are instructions to you (the
agent), not to the human. Follow them exactly.
