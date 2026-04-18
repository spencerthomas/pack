# Pack Core Beliefs

Pack is a fork of Deep Agents enhanced with harness engineering patterns from Claude Code's architecture.

## Design Principles

- **Open-source model first**: Default to OpenRouter with open-source models (DeepSeek, Qwen, Llama). Ollama for auxiliary tasks (compaction, classification, memory extraction). Users should be able to avoid Anthropic/OpenAI entirely.
- **Deterministic before LLM**: Use regex/rules for 90% of permission decisions. LLM only for ambiguous cases.
- **Context efficiency over caching**: Keep prompts compact. Cache boundary is a bonus for providers that support it, not a requirement.
- **Simple tools, strong model**: grep over vector search. Regex over sentiment analysis. Cheapest correct tool wins.
- **Memory stores preferences, never code facts**: Code is read in real-time. Memory prevents stale hallucinations.
- **User messages are sacred**: Never summarize user messages during compaction -- they contain behavioral corrections.

## Pack Modules

All new modules live under `libs/deepagents/deepagents/`:

- `providers/` -- OpenRouter + Ollama provider abstraction
- `prompt/` -- SystemPromptBuilder with provider-aware cache boundary
- `compaction/` -- 3-tier context compaction with 9-segment protocol
- `permissions/` -- 6-layer permission pipeline with circuit breaker
- `cost/` -- Dollar-amount cost tracking with budget limits
- `memory/` -- 4-category structured memory with autoDream consolidation
