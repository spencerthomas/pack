# Rules for coding tasks

These rules apply to every coding task unless a more specific pack
overrides them. Treat them as hard constraints on your working style,
not optional suggestions.

## Before you write code

1. Read the task prompt in full. If it references files, list or read
   those files first.
2. If a test file exists (`/tests/test.sh`, `pytest.ini`, `test_*.py`),
   run it to see current state before editing.
3. For multi-file changes, emit a brief plan naming the files you
   intend to touch before you touch them.

## While you write code

1. Prefer structured file tools (`read_file`, `write_file`, `edit_file`)
   over shell output redirects for file writes.
2. Do not re-read the same file multiple times in a row — cache what
   you've already seen.
3. If a tool call returns an error, read the error message carefully
   before retrying. Blind retries are wasted budget.
4. Commit to one approach and finish it. Pivoting mid-task is expected
   when you hit a blocker; restarting because you changed your mind
   is not.

## Before you declare done

1. Run the task's verification tests if any exist. Report their output
   to yourself before emitting the final response.
2. Walk through the original task requirements one by one. For each,
   name the file or output that satisfies it.
3. If there are edge cases the task mentioned, confirm each is handled.
4. Output format matters. Match the task's specified format exactly —
   file path, identifier, JSON shape, CSV columns, exit code.

## Working with the sandbox

1. The working directory inside the sandbox is `/app` unless the task
   says otherwise.
2. Do not assume Python, Node, or other runtimes are at any particular
   path — use `which` or `command -v` if you need to verify.
3. Network access may be limited. If a download fails, do not waste
   steps retrying; look for the resource already present in the
   container or in mounted paths.

## Anti-patterns to avoid

- **Single-shot dumping**: producing tens of thousands of tokens of
  analysis in one response without any tool calls. If the task needs
  code, write the code; don't narrate it.
- **Speculative rewriting**: changing code you don't need to change
  because "it could be better." Stay inside task scope.
- **Unverified claims**: stating "the tests pass" without having run
  them. If you didn't observe the output, don't claim the outcome.
