# Terminal Bench 2.0 Benchmark Tracking

Tracks Pack's performance on Terminal Bench 2.0 across harness versions and model configurations.

## Structure

```
tb2/
├── task_registry.json    # All 89 TB2 tasks with metadata
├── runs/                 # Per-run results (one JSON per benchmark run)
│   ├── run-001.json      # 2026-04-18: First run post-CWD fix
│   └── ...
├── analysis/             # Cross-run analysis scripts and reports
└── README.md
```

## Run Format

Each `runs/run-NNN.json` contains:
- `run_id`, `date`, `model`, `provider`, `harness_version`
- `tasks_attempted`, `tasks_passed`, `tasks_failed`, `pass_rate`
- `total_cost_usd`
- `results`: per-task dict with `result` (pass/fail/error), `steps`, `prompt_tokens`, `completion_tokens`

## Comparing Runs

```bash
python3 benchmarks/tb2/analysis/compare_runs.py run-001 run-002
```

## Running a Benchmark

Use `--dataset terminal-bench@2.0` (registry) for compliant runs. Never use `-p` (local path) for formal submissions — local clones skip integrity verification.

```bash
cd libs/evals && OPENROUTER_API_KEY=... PACK_ENABLED=1 \
uv run harbor run \
  --agent-import-path deepagents_harbor:DeepAgentsWrapper \
  --dataset terminal-bench@2.0 \
  -n 3 \
  --jobs-dir /tmp/pack-harbor-run \
  --model "openrouter:z-ai/glm-5.1" \
  --job-name "run-NNN"
```

To save results for tracking:

```bash
python3 benchmarks/tb2/analysis/save_run.py /tmp/pack-harbor-run/run-NNN run-NNN "harness-version" "notes"
```
