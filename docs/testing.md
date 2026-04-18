# Testing Requirements

Every new feature or bugfix MUST be covered by unit tests.

- Unit tests: `tests/unit_tests/` (no network calls allowed)
- Integration tests: `tests/integration_tests/` (network calls permitted)
- We use `pytest` as the testing framework; if in doubt, check other existing tests for examples.
- Do NOT add `@pytest.mark.asyncio` to async tests -- every package sets `asyncio_mode = "auto"` in `pyproject.toml`, so pytest-asyncio discovers them automatically.
- The testing file structure should mirror the source code structure.
- Avoid mocks as much as possible
- Test actual implementation, do not duplicate logic into tests

Ensure the following:

- Does the test suite fail if your new logic is broken?
- Edge cases and error conditions are tested
- Tests are deterministic (no flaky tests)
