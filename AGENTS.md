# Pack - Enhanced Deep Agents

Pack is a fork of Deep Agents enhanced with harness engineering patterns from Claude Code's architecture. Open-source model first, deterministic before LLM, context-efficient.

## Where to Look

- **Core beliefs and design principles**: [docs/core-beliefs.md](docs/core-beliefs.md)
- **Architecture and module map**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **Dependency directions and boundaries**: [docs/DOMAIN_RULES.md](docs/DOMAIN_RULES.md)
- **Code quality, types, docstrings, security**: [docs/code-quality.md](docs/code-quality.md)
- **Testing requirements**: [docs/testing.md](docs/testing.md)
- **Development patterns, tools, commits**: [docs/PATTERNS.md](docs/PATTERNS.md)
- **CLI/Textual patterns**: [docs/cli-patterns.md](docs/cli-patterns.md)
- **CI/CD, releases, partner onboarding**: [docs/ci-cd.md](docs/ci-cd.md)
- **Quality scores by module**: [docs/quality/quality-scores.md](docs/quality/quality-scores.md)
- **Subsystem specs (ghost libraries)**: [docs/specs/](docs/specs/)
- **Documented solutions**: [docs/solutions/](docs/solutions/) — past problems and best practices, organized by category with YAML frontmatter (module, tags, problem_type)
- **Skills**: [skills/](skills/)

## Key Rules

- All Python code MUST include type hints and return types
- Every new feature or bugfix MUST be covered by unit tests
- Never import heavy packages at CLI module level (startup performance)
- PR titles follow Conventional Commits with required scope, lowercase
- Prefer inline `# noqa: RULE` over per-file-ignores for lint suppressions
- Preserve function signatures for public APIs -- no breaking changes
- Use `Content.from_markup` with `$var` for user-controlled strings in Textual widgets

## Pack Modules

All Pack additions live under `libs/deepagents/deepagents/`:

| Module | Role |
|--------|------|
| `providers/` | OpenRouter + Ollama provider abstraction |
| `prompt/` | SystemPromptBuilder with cache boundary |
| `compaction/` | 3-tier context compaction, 9-segment protocol |
| `permissions/` | 6-layer permission pipeline, circuit breaker |
| `cost/` | Dollar-amount cost tracking, budget limits |
| `memory/` | 4-category structured memory, autoDream |
| `hooks/` | Event hook engine |
| `coordination/` | Multi-agent mailbox |
| `execution/` | Parallel tool executor |
| `middleware/pack/` | LangGraph middleware wrappers |
| `agents/` | Agent profiles (Explore, Plan, Review, General) |

See [ARCHITECTURE.md](ARCHITECTURE.md) for dependency directions and middleware composition order.
