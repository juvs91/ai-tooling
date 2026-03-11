#!/bin/bash
# Test script to exhaustively validate response pipeline integration
# Usage: ./test_response_pipeline_integration.sh

set -e

PROXY_URL="${PROXY_URL:-http://127.0.0.1:8083}"

echo "=== Response Pipeline Integration Test Suite ==="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Test counters
PASS=0
FAIL=0

# Helper functions
test_result() {
    local name="$1"
    local result="$2"

    if [ "$result" = "0" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $name"
        ((PASS++))
    else
        echo -e "${RED}✗ FAIL${NC}: $name"
        ((FAIL++))
    fi
}

echo "Test 1: Proxy Health Check"
curl -s "$PROXY_URL/health" > /dev/null
test_result "Proxy health check" $?

echo ""
echo "Test 2: Non-Streaming with Tools"
response=$(curl -s -X POST "$PROXY_URL/v1/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "glm-4.7",
        "max_tokens": 300,
        "tools": [{
            "name": "bash",
            "description": "Run a bash command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }],
        "messages": [{"role": "user", "content": "List files"}]
    }')

has_tool=$(echo "$response" | jq -e '.content[]? | select(.type == "tool_use")' > /dev/null && echo "true" || echo "false")
test_result "Non-streaming with tools - tool extracted" $([ "$has_tool" = "true" ] && echo 0 || echo 1)

echo ""
echo "Test 3: Streaming with Tools"
stream_response=$(curl -s -X POST "$PROXY_URL/v1/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "glm-4.7",
        "max_tokens": 300,
        "stream": true,
        "tools": [{
            "name": "bash",
            "description": "Run a bash command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }],
        "messages": [{"role": "user", "content": "List files"}]
    }' | grep "tool_use" | head -1)

has_stream_tool=$(echo "$stream_response" | grep -q "tool_use" && echo "true" || echo "false")
test_result "Streaming with tools - tool extracted" $([ "$has_stream_tool" = "true" ] && echo 0 || echo 1)

echo ""
echo "Test 4: Metrics Tracking"
metrics=$(curl -s "$PROXY_URL/api/stats")
total=$(echo "$metrics" | jq -r '.total_requests')
native=$(echo "$metrics" | jq -r '.tool_quality.native')
total_tools=$(echo "$metrics" | jq -r '.tool_quality.total')

test_result "Metrics - requests tracked" $([ "$total" -gt "0" ] && echo 0 || echo 1)
test_result "Metrics - tools tracked" $([ "$total_tools" -gt "0" ] && echo 0 || echo 1)

echo ""
echo "Test 5: Check Transformer Logs"
logs=$(docker logs ai-tooling-proxy_cloud-1 --tail 100 2>&1)
has_transformer_logs=$(echo "$logs" | grep -q "universal-tool-extraction" && echo "true" || echo "false")
test_result "Transformer logs visible" $([ "$has_transformer_logs" = "true" ] && echo 0 || echo 1)

echo ""
echo "Test 6: No Errors in Logs"
has_errors=$(echo "$logs" | grep -i "error\|exception\|traceback" | grep -v "WARNING: Ignoring existing" | grep -q . && echo "true" || echo "false")
test_result "No errors in logs" $([ "$has_errors" = "false" ] && echo 0 || echo 1)

echo ""
echo "=== Summary ==="
echo -e "${GREEN}Passed: $PASS${NC}"
echo -e "${RED}Failed: $FAIL${NC}"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed${NC}"
    exit 1
fi
