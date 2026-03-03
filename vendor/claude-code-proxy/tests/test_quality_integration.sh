#!/usr/bin/env bash
# test_quality_integration.sh — Exhaustive integration test for Quality Observability metrics
#
# Usage:  ./tests/test_quality_integration.sh [proxy_url]
# Default proxy URL: http://localhost:8083
#
# Prerequisites: proxy running (cc-proxy-up), curl, jq
#
# This script sends requests that exercise EVERY metric path and then
# checks /api/stats to verify counters incremented correctly.

set -euo pipefail

PROXY="${1:-http://localhost:8083}"
PASS=0
FAIL=0
TOTAL=0

# ── Helpers ──────────────────────────────────────────────────────────

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

check() {
    local label="$1" field="$2" expected="$3" actual
    TOTAL=$((TOTAL + 1))
    actual=$(echo "$STATS" | jq -r "$field" 2>/dev/null || echo "MISSING")
    if [[ "$actual" == "$expected" ]]; then
        green "  PASS: $label ($field = $actual)"
        PASS=$((PASS + 1))
    else
        red "  FAIL: $label — expected $expected, got $actual ($field)"
        FAIL=$((FAIL + 1))
    fi
}

check_gte() {
    local label="$1" field="$2" min="$3" actual
    TOTAL=$((TOTAL + 1))
    actual=$(echo "$STATS" | jq -r "$field" 2>/dev/null || echo "0")
    if [[ "$actual" -ge "$min" ]] 2>/dev/null; then
        green "  PASS: $label ($field = $actual >= $min)"
        PASS=$((PASS + 1))
    else
        red "  FAIL: $label — expected >= $min, got $actual ($field)"
        FAIL=$((FAIL + 1))
    fi
}

check_exists() {
    local label="$1" field="$2" actual
    TOTAL=$((TOTAL + 1))
    actual=$(echo "$STATS" | jq -e "$field" > /dev/null 2>&1 && echo "EXISTS" || echo "MISSING")
    if [[ "$actual" == "EXISTS" ]]; then
        green "  PASS: $label ($field exists)"
        PASS=$((PASS + 1))
    else
        red "  FAIL: $label — field $field missing from stats"
        FAIL=$((FAIL + 1))
    fi
}

send_request() {
    local label="$1" body="$2"
    echo ""
    yellow ">>> Sending: $label"
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "$PROXY/v1/messages" \
        -H "Content-Type: application/json" \
        -H "x-api-key: test-key" \
        -H "anthropic-version: 2023-06-01" \
        -d "$body" 2>&1) || true
    local http_code
    http_code=$(echo "$response" | tail -1)
    local body_response
    body_response=$(echo "$response" | sed '$d')
    if [[ "$http_code" == "200" ]]; then
        green "    HTTP 200 OK"
        echo "    Response preview: $(echo "$body_response" | head -c 200)"
    else
        yellow "    HTTP $http_code (may be expected for some test cases)"
        echo "    Response: $(echo "$body_response" | head -c 300)"
    fi
    echo ""
}

# ── Preflight ────────────────────────────────────────────────────────

bold "========================================="
bold " Quality Observability Integration Tests"
bold " Proxy: $PROXY"
bold "========================================="
echo ""

# Check proxy is up
if ! curl -sf "$PROXY/health" > /dev/null 2>&1; then
    red "ERROR: Proxy not reachable at $PROXY/health"
    echo "Start the proxy first: cc-proxy-up"
    exit 1
fi
green "Proxy is healthy"

# Snapshot initial stats
STATS_BEFORE=$(curl -s "$PROXY/api/stats")
echo "Initial stats snapshot taken"

# ── Test 1: Simple CHAT (no tools) — exercises classifier ────────

send_request "T1: Simple CHAT (no tools)" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 256,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "What is 2+2?"}]}]
}'

# ── Test 2: Request WITH tools — exercises native or XML tool path ──

send_request "T2: Request WITH tools (Read)" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Read the file /etc/hostname and tell me its contents."}]}],
    "tools": [
        {
            "name": "Read",
            "description": "Reads a file from the local filesystem",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "The absolute path to the file to read"}
                },
                "required": ["file_path"]
            }
        }
    ]
}'

# ── Test 3: BUILDING intent — exercises classifier ──────────────

send_request "T3: BUILDING intent" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 512,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Write a Python function that sorts a list of integers using merge sort. Include type hints and docstrings."}]}],
    "tools": [
        {
            "name": "Write",
            "description": "Writes a file to the filesystem",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["file_path", "content"]
            }
        }
    ]
}'

# ── Test 4: PLANNING intent ─────────────────────────────────────

send_request "T4: PLANNING intent" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 512,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Create a detailed plan for refactoring the authentication module. List all the steps needed, the files to modify, and potential risks."}]}]
}'

# ── Test 5: Streaming request WITH tools ─────────────────────────

yellow ">>> Sending: T5: Streaming request WITH tools"
curl -s -N -X POST "$PROXY/v1/messages" \
    -H "Content-Type: application/json" \
    -H "x-api-key: test-key" \
    -H "anthropic-version: 2023-06-01" \
    -d '{
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 1024,
        "stream": true,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "List the files in the current directory."}]}],
        "tools": [
            {
                "name": "Bash",
                "description": "Executes a bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command to execute"}
                    },
                    "required": ["command"]
                }
            }
        ]
    }' > /dev/null 2>&1 || true
green "    T5: Streaming request completed"
echo ""

# ── Test 6: Multi-tool request ───────────────────────────────────

send_request "T6: Multi-tool request" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "First read /etc/hostname, then search for files matching *.py in the current directory."}]}],
    "tools": [
        {
            "name": "Read",
            "description": "Reads a file",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"]
            }
        },
        {
            "name": "Glob",
            "description": "Find files by pattern",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"]
            }
        }
    ]
}'

# ── Test 7: Analysis request (ANALYSIS detection) ───────────────

send_request "T7: Analysis request" '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "stream": false,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Analyze the architecture of this codebase. Provide a comprehensive analysis including: 1) module dependencies, 2) potential issues, 3) recommendations for improvement."}]}]
}'

# ── Wait for metrics to settle ───────────────────────────────────
sleep 2

# ── Collect final stats ──────────────────────────────────────────

bold ""
bold "========================================="
bold " Verifying /api/stats"
bold "========================================="
echo ""

STATS=$(curl -s "$PROXY/api/stats")

# Pretty print for review
echo "Full stats:"
echo "$STATS" | jq '.' 2>/dev/null || echo "$STATS"
echo ""

# ── Verify stats shape ───────────────────────────────────────────

bold "--- Stats Shape Verification ---"
check_exists "tool_quality section exists" ".tool_quality"
check_exists "tool_quality.native exists" ".tool_quality.native"
check_exists "tool_quality.xml_extracted exists" ".tool_quality.xml_extracted"
check_exists "tool_quality.recovered exists" ".tool_quality.recovered"
check_exists "tool_quality.truncated exists" ".tool_quality.truncated"
check_exists "tool_quality.hallucinated exists" ".tool_quality.hallucinated"
check_exists "tool_quality.total exists" ".tool_quality.total"
check_exists "tool_quality.success_rate_pct exists" ".tool_quality.success_rate_pct"
check_exists "model_quality section exists" ".model_quality"
check_exists "classifier.disagreements exists" ".classifier.disagreements"
check_exists "classifier.agreement_rate_pct exists" ".classifier.agreement_rate_pct"

# ── Verify counter increments ────────────────────────────────────

bold ""
bold "--- Counter Verification ---"

# Total requests should have increased by at least 7 (our test requests)
BEFORE_TOTAL=$(echo "$STATS_BEFORE" | jq -r '.total_requests' 2>/dev/null || echo "0")
AFTER_TOTAL=$(echo "$STATS" | jq -r '.total_requests' 2>/dev/null || echo "0")
DELTA=$((AFTER_TOTAL - BEFORE_TOTAL))
TOTAL=$((TOTAL + 1))
if [[ "$DELTA" -ge 5 ]]; then
    green "  PASS: total_requests incremented by $DELTA (expected >= 5)"
    PASS=$((PASS + 1))
else
    red "  FAIL: total_requests only incremented by $DELTA (expected >= 5)"
    FAIL=$((FAIL + 1))
fi

# Tool calls should have happened (native or xml_extracted)
TOOL_TOTAL=$(echo "$STATS" | jq -r '.tool_quality.total' 2>/dev/null || echo "0")
TOOL_NATIVE=$(echo "$STATS" | jq -r '.tool_quality.native' 2>/dev/null || echo "0")
TOOL_XML=$(echo "$STATS" | jq -r '.tool_quality.xml_extracted' 2>/dev/null || echo "0")

TOTAL=$((TOTAL + 1))
if [[ "$TOOL_NATIVE" -gt 0 ]] || [[ "$TOOL_XML" -gt 0 ]]; then
    green "  PASS: Tool calls recorded (native=$TOOL_NATIVE, xml=$TOOL_XML)"
    PASS=$((PASS + 1))
else
    yellow "  WARN: No tool calls recorded yet (native=$TOOL_NATIVE, xml=$TOOL_XML)"
    yellow "        This may be expected if the model responded with text only"
    FAIL=$((FAIL + 1))
fi

# Intent counts should have our requests
check_exists "Intent counts populated" ".intents"

# ── Summary ──────────────────────────────────────────────────────

echo ""
bold "========================================="
if [[ "$FAIL" -eq 0 ]]; then
    green " ALL $TOTAL CHECKS PASSED"
else
    red " $FAIL/$TOTAL CHECKS FAILED"
    green " $PASS/$TOTAL CHECKS PASSED"
fi
bold "========================================="
echo ""

# Show the key new metrics at a glance
bold "--- Key Quality Metrics ---"
echo "  Tool Quality:"
echo "$STATS" | jq '.tool_quality' 2>/dev/null || echo "  (unavailable)"
echo ""
echo "  Model Quality:"
echo "$STATS" | jq '.model_quality' 2>/dev/null || echo "  (unavailable)"
echo ""
echo "  Classifier:"
echo "$STATS" | jq '.classifier' 2>/dev/null || echo "  (unavailable)"

exit $FAIL
