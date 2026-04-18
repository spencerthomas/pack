# CI/CD Infrastructure

## Release process

Releases use **release-please** automation. When conventional commits land on `main`, release-please creates/updates a release PR with version bumps and CHANGELOG entries. Merging the release PR triggers `.github/workflows/release.yml` via `.github/workflows/release-please.yml`.

The release pipeline: build -> unit tests against built package -> publish to Test PyPI -> publish to PyPI (trusted publishing/OIDC) -> create GitHub release.

See `.github/RELEASING.md` for the full workflow (version bumping, pre-releases, troubleshooting failed releases, and label management).

## PR labeling and linting

**Title linting** (`.github/workflows/pr_lint.yml`) -- Enforces Conventional Commits format with required scope on PR titles

**Auto-labeling:**

- `.github/workflows/pr_labeler.yml` -- Unified PR labeler (size, file, title, external/internal, contributor tier)
- `.github/workflows/pr_labeler_backfill.yml` -- Manual backfill of PR labels on open PRs
- `.github/workflows/auto-label-by-package.yml` -- Issue labeling by package
- `.github/workflows/tag-external-issues.yml` -- Issue external/internal classification and contributor tier labeling

## Adding a new partner to CI

When adding a new partner package, update these files:

- `.github/ISSUE_TEMPLATE/bug-report.yml` -- Add to Area checkbox options
- `.github/ISSUE_TEMPLATE/feature-request.yml` -- Add to Area checkbox options
- `.github/ISSUE_TEMPLATE/privileged.yml` -- Add to Area checkbox options
- `.github/dependabot.yml` -- Add dependency update directory
- `.github/scripts/pr-labeler-config.json` -- Add scope-to-label mapping and file rule
- `.github/workflows/auto-label-by-package.yml` -- Add package label mapping
- `.github/workflows/ci.yml` -- Add to change detection and lint/test jobs
- `.github/workflows/pr_lint.yml` -- Add to allowed scopes
- `.github/workflows/release.yml` -- Add to `package` input options and `setup` job mapping
- `.github/workflows/release-please.yml` -- Add release detection output and trigger job
- `release-please-config.json` -- Add package entry under `packages`
- `.release-please-manifest.json` -- Add initial version entry
- `.github/RELEASING.md` -- Add to Managed Packages table
- `.github/workflows/harbor.yml` -- Add sandbox option and credential check (sandbox-backed partners only)

## GitHub Actions and Workflows

This repository requires actions to be pinned to a full-length commit SHA. Attempting to use a tag will fail. Use the `gh` CLI to query. Verify tags are not annotated tag objects (which would need dereferencing).

## Evals (`libs/evals/`)

**Vendored data files:**

`libs/evals/tests/evals/tau2_airline/data/` contains vendored data from the upstream [tau-bench](https://github.com/sierra-research/tau-bench) project. These files must stay byte-identical to upstream. Pre-commit hooks (`end-of-file-fixer`, `trailing-whitespace`, `fix-smartquotes`, `fix-spaces`) are excluded from this directory in `.pre-commit-config.yaml`. Do not remove those exclusions or reformat files in this directory.
