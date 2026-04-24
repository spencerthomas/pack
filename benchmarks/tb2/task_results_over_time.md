# Pack TB2 Benchmark — Task Results Over Time

## Run Summary

| Run | Date | Tasks | Pass | Rate | Notes |
|-----|------|-------|------|------|-------|
| run-001 | 2026-04-18 | 56 | 34 | 61% | First full benchmark run after CWD fix + agent-first architecture engine. Pre ti |
| run-003 | 2026-04-19 | 88 | 24 | 27% | Remaining 33 tasks + retries. OpenRouter timeout fix active, no auto-verificatio |
| run-004 | 2026-04-19 | 34 | 9 | 26% | 33 remaining tasks, OpenRouter timeout fix + max_tokens cap, no auto-verificatio |
| run-008 | 2026-04-20 | 5 | 2 | 40% | Middleware validation (n=5) |
| run-009 | 2026-04-20 | 10 | 6 | 60% | Middleware scale (n=10) |

## Per-Task Outcomes

Symbols: ✓ pass, ✗ fail, — not run.

| Task | run-001 | run-003 | run-004 | run-008 | run-009 |
|------|------|------|------|------|------|
| adaptive-rejection-sampler | ✗ | ✗ | — | — | — |
| bn-fit-modify | ✓ | ✓ | — | — | — |
| break-filter-js-from-html | ✓ | — | ✓ | — | ✓ |
| build-cython-ext | ✓ | ✓ | — | — | — |
| build-pmars | — | ✓ | — | — | — |
| build-pov-ray | — | ✗ | — | — | — |
| caffe-cifar-10 | — | ✗ | — | — | — |
| cancel-async-tasks | ✓ | — | ✗ | ✓ | — |
| chess-best-move | — | — | — | — | — |
| circuit-fibsqrt | — | ✗ | — | — | — |
| cobol-modernization | — | ✗ | — | — | — |
| code-from-image | — | — | — | — | — |
| compile-compcert | ✗ | — | — | — | — |
| configure-git-webserver | — | ✗ | — | — | — |
| constraints-scheduling | ✓ | — | ✓ | — | — |
| count-dataset-tokens | ✓ | — | — | — | — |
| crack-7z-hash | ✓ | ✓ | — | — | — |
| custom-memory-heap-crash | ✓ | — | — | — | — |
| db-wal-recovery | ✗ | ✓ | — | — | — |
| distribution-search | — | ✗ | — | — | — |
| dna-assembly | ✗ | — | ✗ | — | — |
| dna-insert | — | ✗ | — | — | — |
| extract-elf | ✓ | — | — | — | — |
| extract-moves-from-video | — | — | — | — | — |
| feal-differential-cryptanalysis | ✓ | ✓ | — | — | — |
| feal-linear-cryptanalysis | — | ✗ | — | — | — |
| filter-js-from-html | — | ✗ | — | — | — |
| financial-document-processor | — | — | — | — | — |
| fix-code-vulnerability | ✓ | ✓ | — | — | — |
| fix-git | — | ✓ | — | — | — |
| gcode-to-text | ✗ | — | — | — | — |
| git-leak-recovery | — | ✓ | — | — | — |
| git-multibranch | ✓ | — | ✓ | ✗ | — |
| gpt2-codegolf | — | ✗ | — | — | ✗ |
| headless-terminal | — | ✓ | — | — | — |
| hf-model-inference | ✓ | ✓ | — | — | — |
| install-windows-3.11 | — | — | — | — | — |
| kv-store-grpc | ✓ | ✗ | — | — | — |
| large-scale-text-editing | ✓ | — | ✓ | — | — |
| largest-eigenval | — | ✗ | — | — | ✗ |
| llm-inference-batching-scheduler | ✓ | ✓ | — | — | ✓ |
| log-summary-date-ranges | ✓ | — | ✓ | — | ✓ |
| mailman | ✓ | ✓ | — | — | — |
| make-doom-for-mips | — | ✗ | — | — | — |
| make-mips-interpreter | ✗ | — | ✗ | — | — |
| mcmc-sampling-stan | — | ✓ | — | — | — |
| merge-diff-arc-agi-task | ✓ | — | — | — | ✓ |
| model-extraction-relu-logits | ✗ | — | ✗ | — | — |
| modernize-scientific-stack | ✓ | ✓ | — | — | — |
| mteb-leaderboard | — | ✗ | — | — | — |
| mteb-retrieve | — | ✗ | — | — | — |
| multi-source-data-merger | ✓ | ✓ | — | — | — |
| nginx-request-logging | ✓ | — | ✓ | — | — |
| openssl-selfsigned-cert | ✗ | — | ✗ | ✗ | — |
| overfull-hbox | — | ✗ | — | — | — |
| password-recovery | — | ✓ | — | — | — |
| path-tracing | ✓ | — | ✓ | — | — |
| path-tracing-reverse | ✗ | — | — | — | — |
| polyglot-c-py | ✗ | — | ✗ | ✗ | — |
| polyglot-rust-c | — | ✗ | — | — | — |
| portfolio-optimization | ✓ | ✓ | — | — | — |
| protein-assembly | ✗ | — | — | — | — |
| prove-plus-comm | — | ✓ | — | — | — |
| pypi-server | ✓ | ✓ | ✓ | — | — |
| pytorch-model-cli | ✓ | — | — | — | ✓ |
| pytorch-model-recovery | ✓ | ✓ | — | — | — |
| qemu-alpine-ssh | ✗ | ✗ | — | — | — |
| qemu-startup | — | ✗ | — | — | — |
| query-optimize | — | ✓ | — | — | — |
| raman-fitting | — | ✗ | — | — | — |
| regex-chess | ✗ | — | — | — | — |
| regex-log | ✓ | — | ✗ | — | — |
| reshard-c4-data | ✓ | — | — | — | ✗ |
| rstan-to-pystan | ✓ | — | — | — | — |
| sam-cell-seg | — | — | — | — | — |
| sanitize-git-repo | — | ✓ | — | — | — |
| schemelike-metacircular-eval | ✗ | ✗ | — | — | — |
| sparql-university | ✓ | — | ✓ | — | — |
| sqlite-db-truncate | ✓ | — | — | — | — |
| sqlite-with-gcov | ✗ | — | ✗ | ✓ | — |
| torch-pipeline-parallelism | — | ✗ | — | — | — |
| torch-tensor-parallelism | — | ✗ | — | — | — |
| train-fasttext | ✗ | ✗ | — | — | — |
| tune-mjcf | ✗ | — | — | — | — |
| video-processing | — | — | — | — | — |
| vulnerable-secret | ✓ | — | — | — | — |
| winning-avg-corewars | ✓ | ✓ | — | — | ✓ |
| write-compressor | — | ✗ | — | — | ✗ |

**Unique tasks:** 88  |  **Runs:** 5

## Recent Shifts (run-004 → run-009)

Overlap: 5 tasks
  Newly passing (fail→pass): 0 — []
  Newly failing (pass→fail): 0 — []
