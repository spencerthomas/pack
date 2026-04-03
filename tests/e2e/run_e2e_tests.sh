#!/usr/bin/env bash
# Pack End-to-End Test Suite
# Runs real SWE workflows against the Pack CLI with a live model.
#
# Usage: OPENROUTER_API_KEY=sk-or-... bash tests/e2e/run_e2e_tests.sh
#
# Each test runs the CLI in non-interactive (-n) mode with a prompt,
# captures output, and checks for expected behavior.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_CLI_DIR="$(cd "$SCRIPT_DIR/../../libs/cli" && pwd)"
TEST_PROJECT="/Users/c/dev/pack-e2e-test"
MODEL="${PACK_MODEL:-deepseek/deepseek-chat}"
PASS=0
FAIL=0
SKIP=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo -e "${RED}ERROR: OPENROUTER_API_KEY not set${NC}"
    exit 1
fi

export OPENROUTER_API_KEY
export PACK_ENABLED=1

run_test() {
    local name="$1"
    local prompt="$2"
    local expect_pattern="$3"
    local cwd="${4:-$TEST_PROJECT}"
    local timeout="${5:-120}"

    echo -e "\n${CYAN}━━━ TEST: $name ━━━${NC}"
    echo -e "  Prompt: $prompt"
    echo -e "  Expect: $expect_pattern"

    local output
    if output=$(cd "$cwd" && timeout "$timeout" uv run --directory "$PACK_CLI_DIR" \
        deepagents -n --model "$MODEL" "$prompt" 2>&1); then
        :
    fi

    if echo "$output" | grep -qiE "$expect_pattern"; then
        echo -e "  ${GREEN}PASS${NC}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC}"
        echo -e "  Output (last 20 lines):"
        echo "$output" | tail -20 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

run_test_no_model() {
    # Tests that don't need a model call (slash commands, etc.)
    local name="$1"
    local prompt="$2"
    local expect_pattern="$3"

    echo -e "\n${CYAN}━━━ TEST: $name ━━━${NC}"
    echo -e "  Prompt: $prompt"
    echo -e "  Expect: $expect_pattern"

    local output
    if output=$(cd "$TEST_PROJECT" && timeout 30 uv run --directory "$PACK_CLI_DIR" \
        deepagents -n --model "$MODEL" "$prompt" 2>&1); then
        :
    fi

    if echo "$output" | grep -qiE "$expect_pattern"; then
        echo -e "  ${GREEN}PASS${NC}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC}"
        echo -e "  Output (last 10 lines):"
        echo "$output" | tail -10 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Pack E2E Test Suite                     ║${NC}"
echo -e "${CYAN}║  Model: $MODEL${NC}"
echo -e "${CYAN}║  Project: $TEST_PROJECT${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

# ─── Test 1: Basic code generation ───
run_test \
    "Basic code generation" \
    "Read src/calculator.py and tell me what functions are defined in it. Just list the function names." \
    "add|subtract|multiply|divide|factorial|is_prime"

# ─── Test 2: Bug detection ───
run_test \
    "Bug detection" \
    "Read src/calculator.py and identify the bugs. Don't fix them, just list what's wrong." \
    "zero|division|recursive|recursion|factorial|n-1|sqrt|infinite"

# ─── Test 3: Fix a bug ───
run_test \
    "Fix divide-by-zero bug" \
    "Fix the divide function in src/calculator.py to handle division by zero. Raise a ValueError." \
    "ValueError|ZeroDivisionError|zero|fixed" \
    "$TEST_PROJECT" \
    120

# ─── Test 4: Write tests ───
run_test \
    "Write missing tests" \
    "Read tests/test_calculator.py and src/calculator.py. The test file is missing tests for multiply, divide, factorial, and is_prime. Add them." \
    "test_multiply|test_divide|test_factorial|test_prime|assert" \
    "$TEST_PROJECT" \
    120

# ─── Test 5: File search ───
run_test \
    "File search with glob/grep" \
    "Find all .py files in this project and tell me how many there are." \
    "[0-9]|calculator|test_calculator|\.py"

# ─── Test 6: Git awareness ───
run_test \
    "Git status awareness" \
    "What git branch am I on and are there any uncommitted changes?" \
    "main|master|branch|commit|modified|clean|dirty|change"

echo -e "\n${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Results                                 ║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║  ${GREEN}PASS: $PASS${CYAN}                                ║${NC}"
echo -e "${CYAN}║  ${RED}FAIL: $FAIL${CYAN}                                ║${NC}"
echo -e "${CYAN}║  Total: $((PASS + FAIL))                               ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
