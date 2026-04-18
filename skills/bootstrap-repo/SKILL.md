---
name: bootstrap-repo
description: Read target repo structure, extract conventions, synthesize operating context
triggers: First interaction with a new repository
allowed-tools: [read_file, list_directory, shell]
---

# Bootstrap Repo

Scan a repository to extract its structure, conventions, toolchain, and constraints, then produce a concise operating context that other skills and agents can consume.

## Purpose

An agent operating in an unfamiliar codebase wastes cycles guessing at conventions (test framework, linter config, build system, directory layout). Bootstrap-repo front-loads that discovery into a single pass, producing a structured summary that subsequent skills reference instead of re-scanning.

## Input / Output

**Input:**
- `repo_path`: Root directory of the target repository

**Output:**
- Language(s) and framework(s) detected
- Build / test / lint commands (from package.json, Makefile, pyproject.toml, etc.)
- Directory layout summary (src, tests, docs, config locations)
- Coding conventions observed (naming, module structure, import style)
- Presence of `.pack-rules.md`, `CLAUDE.md`, or other agent configuration files
- Recommended verification command for `verify-and-iterate`

## Interaction with Other Skills

- **architecture-check**: If `.pack-rules.md` is found, flag it for architecture-check
- **plan-and-execute**: Feeds repo context into plan creation for informed step design
- **verify-and-iterate**: Supplies the correct test/build command for the repo

## Example Invocation

```
bootstrap-repo:
  repo_path: "/Users/dev/my-project"
```
