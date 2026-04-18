# Pack Module Quality Scores

Last updated: 2026-04-18

## Grading Criteria

| Grade | Definition |
|-------|-----------|
| **A** | Complete, tested, documented, spec-verified. Full type hints, comprehensive unit tests covering happy path and edge cases, docstrings on all public functions, behavior matches a written spec. |
| **B** | Working, tested, partially documented. Type hints present, meaningful test coverage, docstrings on most public functions but gaps remain. No formal spec. |
| **C** | Working, minimal tests, no docs. May have type hints but few or no docstrings, tests exist but cover only basic paths. |
| **D** | Working but fragile, needs attention. Missing tests, missing type hints or docs, unclear contracts, known failure modes. |
| **--** | Not applicable or not yet assessed. |

### Dimension Definitions

- **Types**: Return type annotations and parameter type hints on public API.
- **Tests**: Unit test coverage in `libs/deepagents/tests/unit_tests/`. Assessed by file count, line count, and breadth of scenarios.
- **Docs**: Docstrings on public classes and functions within the module source.
- **Spec**: Whether a written specification or contract document exists and the implementation is verified against it.
- **Boundary Validation**: Input validation, error handling (`raise`, guard clauses), and defensive checks at module boundaries.

## Quality Assessment Table

| Module | Types | Tests | Docs | Spec | Boundary | Overall |
|--------|-------|-------|------|------|----------|---------|
| `compaction/` | B | C | B | D | D | C |
| `permissions/` | A | A | B | D | C | B |
| `cost/` | A | B | B | D | C | B |
| `memory/` | A | B | B | D | B | B |
| `providers/` | A | D | C | D | C | C |
| `prompt/` | A | D | C | D | D | D |
| `middleware/pack/` | A | B | B | D | C | B |
| `hooks/` | A | B | C | D | D | C |
| `coordination/` | A | C | B | D | D | C |
| `execution/` | A | B | B | D | D | C |
| `agents/` | A | C | A | D | C | C |
| `tools/` | A | C | A | D | B | B |
| `backends/` | A | C | C | D | B | C |
| `graph.py` | A | B | C | D | C | B |

### Module-by-Module Notes

**compaction/** (Overall: C)
16 functions, good type hints, ~81% docstring coverage. Only 178 lines of tests in `test_compaction.py`. Minimal boundary validation (2 guard lines). No spec document. The segment protocol file suggests a contract exists in code but is not documented externally.

**permissions/** (Overall: B)
25 functions, full type hints, strong docstring coverage (~80%). Best-tested module: 972 lines across `test_permissions.py`, `test_permissions_pipeline.py`. Includes classifier, rules engine, circuit breaker, and pipeline -- all tested. Boundary validation is light (3 lines) given the security-critical role.

**cost/** (Overall: B)
21 functions, full type hints, ~86% docstring coverage. 289 test lines in `test_cost.py`. Covers tracker, pricing, and display. Moderate boundary validation. No spec but a well-contained domain.

**memory/** (Overall: B)
38 functions, near-complete type hints (37/38 return types), strong docstrings (34/38). 587 test lines in `test_memory.py` plus `test_pack_memory_middleware.py`. Best boundary validation among non-backend modules (33 lines). Includes index, dream, extractor, taxonomy -- a rich subsystem.

**providers/** (Overall: C)
13 functions, full type hints, weak docstrings (~62%). No dedicated test file -- zero test lines for providers directly. Covers openrouter, ollama, and base provider. Moderate validation. Needs tests urgently.

**prompt/** (Overall: D)
18 functions, full type hints, weak docstrings (~56%). No dedicated test file -- zero test lines. No boundary validation found. Covers sections, builder, cache_strategy. Fragile: untested prompt assembly is a high-risk gap since it directly affects LLM behavior.

**middleware/pack/** (Overall: B)
33 functions, full type hints, good docstrings (~70%). Tested via `test_pack_middleware.py`, `test_pack_memory_middleware.py`, `test_agent_dispatch.py`, and indirectly through integration tests. 8 files covering compaction, cost, agent dispatch, memory, hooks, parallel, permission, and state middleware. Light boundary validation (11 lines).

**hooks/** (Overall: C)
6 functions, full type hints, weak docstrings (3/6). 213 test lines in `test_hooks.py`. Small module (events, engine, runners) but documentation gaps. Minimal boundary validation (1 line).

**coordination/** (Overall: C)
9 functions, full type hints, strong docstrings (8/9). Only 160 test lines in `test_coordination.py`. Covers mailbox and teammate. Very little boundary validation (2 lines) for a concurrency-sensitive module.

**execution/** (Overall: C)
7 functions, full type hints, good docstrings (6/7). Tested indirectly via `test_parallel_execution.py` (204 lines). Single file (`parallel.py`). Light boundary validation (3 lines).

**agents/** (Overall: C)
3 functions, full type hints, full docstrings. Tested indirectly via `test_agent_types.py` and `test_agent_dispatch.py` (289 lines combined). Very small module (profiles.py only). Adequate validation for its size.

**tools/** (Overall: B)
9 functions, full type hints, full docstrings. 256 lines in `test_tools.py` plus extensive testing in `test_file_system_tools.py` (601 lines) and `test_file_system_tools_async.py` (180 lines). Good boundary validation (20 lines). Covers git_worktree and document_reader.

**backends/** (Overall: C)
152 functions -- largest module by far. Full type hints (158 return types), but only ~52% docstring coverage (79/152). No dedicated `test_backends.py`; tested indirectly via `test_local_shell.py` (97 lines), `test_local_sandbox_operations.py` (1323 lines), and integration tests. Strong boundary validation (134 lines). The size and importance of this module (10 files) warrant dedicated unit tests and better documentation.

**graph.py** (Overall: B)
7 functions, full type hints, weak docstrings (3/7). 590 test lines in `test_graph.py` -- solid coverage for a single file. Moderate boundary validation (7 lines). Central orchestration file at 36KB; the low docstring ratio is notable given its importance.

## Known Gaps

### Critical (blocks reliability)

1. **prompt/ has zero tests.** Prompt assembly directly controls LLM input. Any regression here silently degrades agent quality. Needs at least: section ordering tests, cache strategy validation, builder output shape tests.

2. **providers/ has zero tests.** Provider abstraction is on the critical path for every LLM call. Base protocol conformance, error handling for API failures, and provider-specific quirks are all untested.

3. **No spec documents exist for any module.** Every module scores D on Spec. Written contracts for permissions pipeline, middleware ordering, graph assembly, and prompt structure would prevent regressions and clarify intent.

### High Priority (degrades maintainability)

4. **backends/ is the largest module (152 functions) with no dedicated test file.** Relies entirely on indirect coverage through sandbox and shell tests. Protocol conformance, store operations, filesystem edge cases, and composite backend behavior need direct tests.

5. **compaction/ has weak boundary validation.** Context collapse and segment protocol handle token-budget-critical operations with minimal input guards. Off-by-one or malformed segment errors could silently corrupt context.

6. **coordination/ has minimal tests for a concurrency module.** Mailbox and teammate patterns need race condition tests, ordering guarantees, and failure-mode coverage.

7. **hooks/ engine has 1 boundary validation line.** Hook dispatch is infrastructure -- silent failures here mask bugs elsewhere. Needs validation for malformed events, missing handlers, and re-entrant calls.

### Moderate (technical debt)

8. **graph.py docstrings cover only 3/7 functions.** As the central orchestration file (36KB), every public function should document its contract, parameters, and interaction with middleware.

9. **permissions/ boundary validation is thin for a security module.** Classifier and pipeline should validate all inputs aggressively -- malformed rules or unexpected tool names should fail loud.

10. **middleware/pack/ has 8 files but only ~70% docstring coverage.** State management and middleware ordering dependencies are implicit; documenting them prevents subtle ordering bugs.
