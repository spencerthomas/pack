# Development Patterns

## Project structure

This is a Python monorepo with multiple independently versioned packages:

```txt
deepagents/
├── libs/
│   ├── deepagents/  # SDK
│   ├── cli/         # CLI tool
│   ├── acp/         # Agent Context Protocol support
│   ├── evals/       # Evaluation suite and Harbor integration
│   └── partners/    # Integration packages
│       └── daytona/
│       └── ...
├── .github/         # CI/CD workflows and templates
└── README.md        # Information about Deep Agents
```

## Development tools and commands

- `uv` -- Package installer and resolver (replaces pip/poetry)
- `make` -- Task runner. Look at the `Makefile` for available commands and usage patterns.
- `ruff` -- Linter and formatter
- `ty` -- Static type checking

Local development uses editable installs: `[tool.uv.sources]`

```bash
# Run unit tests (no network)
make test

# Run specific test file
uv run --group test pytest tests/unit_tests/test_specific.py

# Lint code
make lint

# Format code
make format
```

## Commit standards

Suggest PR titles that follow Conventional Commits format. Refer to .github/workflows/pr_lint for allowed types and scopes. Note that all commit/PR titles should be in lowercase with the exception of proper nouns/named entities. All PR titles should include a scope with no exceptions. For example:

```txt
feat(sdk): add new chat completion feature
fix(cli): resolve type hinting issue
chore(evals): update infrastructure dependencies
```

See [PR labeling and linting](ci-cd.md#pr-labeling-and-linting) for more info.

Describe the "why" of the changes, why the proposed solution is the right one. Limit prose.

## Additional resources

- **Documentation:** https://docs.langchain.com/oss/python/deepagents/overview and source at https://github.com/langchain-ai/docs or `../docs/`. Prefer the local install and use file search tools for best results. If needed, use the docs MCP server as defined in `.mcp.json` for programmatic access.
- **Contributing Guide:** [Contributing Guide](https://docs.langchain.com/oss/python/contributing/overview)
