#!/usr/bin/env python3
"""
Live quality validation script — sends 16 diverse requests to the proxy
and measures quality metrics per-request and in aggregate.

Usage:
    python tests/test_quality_live.py [--base-url http://localhost:8083]

Requires: requests (pip install requests)
"""
import json
import sys
import time
import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8083"
API = f"{BASE_URL}/v1/messages"
STATS = f"{BASE_URL}/api/stats"
LOGS = f"{BASE_URL}/api/logs"

HEADERS = {
    "x-api-key": "test-key",
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

# CC-like tools for the proxy to route through
CC_TOOLS = [
    {
        "name": "Read",
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to file"},
                "offset": {"type": "number", "description": "Line offset"},
                "limit": {"type": "number", "description": "Line limit"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "Write a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Edit a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path"},
                "old_string": {"type": "string", "description": "Text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Bash",
        "description": "Run a bash command",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run"},
                "description": {"type": "string", "description": "What this does"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory to search"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files by pattern",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "TodoWrite",
        "description": "Track task progress",
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string"},
                            "activeForm": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["todos"],
        },
    },
]

def _build_long_context_messages(turns: int) -> list[dict]:
    """Generate a long conversation (simulates 20+ turn CC session)."""
    messages = []
    messages.append({
        "role": "user",
        "content": "Help me build a web scraper. Start by reading the project files.",
    })
    for i in range(turns):
        messages.append({
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"t_long_{i}",
                    "name": "Read" if i % 2 == 0 else "Grep",
                    "input": {"file_path": f"/app/module_{i}.py"} if i % 2 == 0 else {"pattern": f"class_{i}", "path": "/app"},
                },
            ],
        })
        fake_content = f"# module_{i}.py\nclass Module{i}:\n    def process(self, data):\n        return data.transform(step={i})\n" * 5
        messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"t_long_{i}", "content": fake_content},
            ],
        })
    messages.append({
        "role": "user",
        "content": (
            "Based on all the modules you've read (module_0 through module_19), "
            "write a summary of the data flow and create an integration test "
            "that calls Module0.process() through Module19.process() in sequence. "
            "Use the Write tool."
        ),
    })
    return messages


# ── 16 Test Requests ──────────────────────────────────────────────────
# Each has: name, expected_phase, messages, tools (optional), stream (optional)

REQUESTS = [
    # ── EXPLORE phase (→ deepseek-chat) ──
    {
        "name": "T01-EXPLORE: Simple chat question",
        "expected": "CHAT→SMALL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "What is the difference between a list and a tuple in Python?"}
            ],
        },
    },
    {
        "name": "T02-EXPLORE: Read-only tool history",
        "expected": "EXPLORE→SMALL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Read the server.py file and tell me what endpoints it has."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_01", "name": "Read", "input": {"file_path": "/app/server.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_01", "content": "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n\n@app.post('/v1/messages')\nasync def messages(): pass"},
                    ],
                },
                {"role": "user", "content": "Now summarize the endpoints."},
            ],
        },
    },
    {
        "name": "T03-EXPLORE: Grep + analysis",
        "expected": "EXPLORE→SMALL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Find all TODO comments in the codebase."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_02", "name": "Grep", "input": {"pattern": "TODO", "path": "/app"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_02", "content": "server.py:42: # TODO: add rate limiting\nutils.py:15: # TODO: implement caching\nconfig.py:88: # TODO: validate env vars"},
                    ],
                },
                {"role": "user", "content": "Which of these TODOs is highest priority?"},
            ],
        },
    },
    # ── PLAN phase (→ glm-4.7) ──
    {
        "name": "T04-PLAN: Architecture design",
        "expected": "PLANNING→BIG",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": [
                {"role": "user", "content": "Design a caching layer for our API. We need to cache responses from external providers with TTL support, cache invalidation on config changes, and memory limits. Consider Redis vs in-memory approaches. Give me a detailed implementation plan with file structure, interfaces, and trade-offs."},
            ],
        },
    },
    {
        "name": "T05-PLAN: Refactoring strategy",
        "expected": "PLANNING→BIG",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "I want to refactor our monolithic server.py (800 lines) into a modular architecture. Plan how to split it into separate modules for routing, middleware, handlers, and config. Consider backwards compatibility and testing strategy."},
            ],
        },
    },
    {
        "name": "T06-PLAN: Read then plan",
        "expected": "PLANNING→BIG (reads-only override)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Read the config file and then design a better configuration system."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_03", "name": "Read", "input": {"file_path": "/app/config.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_03", "content": "import os\n\nclass Config:\n    MODEL = os.getenv('MODEL', 'gpt-4')\n    API_KEY = os.getenv('API_KEY')\n    MAX_TOKENS = int(os.getenv('MAX_TOKENS', '4096'))"},
                    ],
                },
                {"role": "user", "content": "Now design a better config system with validation, typing, and env file support."},
            ],
        },
    },
    # ── EXECUTE phase (→ MiniMax-M2.5) ──
    {
        "name": "T07-EXECUTE: Write new file (with tool history)",
        "expected": "BUILDING→BUILDING_MODEL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Create a new utils/cache.py with an LRU cache class."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_04", "name": "Write", "input": {"file_path": "/app/utils/cache.py", "content": "class LRUCache:\n    pass"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_04", "content": "File written successfully."},
                    ],
                },
                {"role": "user", "content": "Now implement the full LRU cache with get, put, delete, and TTL support. Use the Write tool."},
            ],
        },
    },
    {
        "name": "T08-EXECUTE: Edit existing code",
        "expected": "BUILDING→BUILDING_MODEL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Add error handling to the process_request function."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_05", "name": "Read", "input": {"file_path": "/app/handler.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_05", "content": "async def process_request(data):\n    result = await call_api(data)\n    return format_response(result)"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_06",
                            "name": "Edit",
                            "input": {
                                "file_path": "/app/handler.py",
                                "old_string": "async def process_request(data):\n    result = await call_api(data)\n    return format_response(result)",
                                "new_string": "async def process_request(data):\n    try:\n        result = await call_api(data)\n        return format_response(result)\n    except Exception as e:\n        logger.error(f'Request failed: {e}')\n        raise",
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_06", "content": "Edit applied successfully."},
                    ],
                },
                {"role": "user", "content": "Good. Now also add retry logic with exponential backoff to the call_api invocation."},
            ],
        },
    },
    {
        "name": "T09-EXECUTE: Multi-tool task",
        "expected": "BUILDING→BUILDING_MODEL",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Run the tests and fix any failures."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_07", "name": "Bash", "input": {"command": "python -m pytest tests/ -v", "description": "Run tests"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_07", "content": "FAILED tests/test_cache.py::test_ttl_expiry - AssertionError: expected None got 'stale_value'\nFAILED tests/test_handler.py::test_retry - TimeoutError\n2 failed, 15 passed"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_08", "name": "Read", "input": {"file_path": "/app/tests/test_cache.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_08", "content": "def test_ttl_expiry():\n    cache = LRUCache(ttl=1)\n    cache.put('key', 'value')\n    time.sleep(1.1)\n    assert cache.get('key') is None"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_09",
                            "name": "Edit",
                            "input": {
                                "file_path": "/app/utils/cache.py",
                                "old_string": "def get(self, key):\n    return self._store.get(key)",
                                "new_string": "def get(self, key):\n    entry = self._store.get(key)\n    if entry and time.time() > entry.expires_at:\n        del self._store[key]\n        return None\n    return entry.value if entry else None",
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_09", "content": "Edit applied successfully."},
                    ],
                },
                {"role": "user", "content": "Now fix the second test failure (test_retry TimeoutError)."},
            ],
        },
    },
    # ── STREAMING tests ──
    {
        "name": "T10-STREAM-EXPLORE: Streaming chat",
        "expected": "CHAT→SMALL (streaming)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "stream": True,
            "messages": [
                {"role": "user", "content": "Explain how Python generators work with a simple example."},
            ],
        },
    },
    {
        "name": "T11-STREAM-EXECUTE: Streaming with tools",
        "expected": "BUILDING→BUILDING_MODEL (streaming)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "stream": True,
            "tools": CC_TOOLS,
            "messages": [
                {"role": "user", "content": "Create a Python decorator for rate limiting."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_10", "name": "Write", "input": {"file_path": "/app/utils/ratelimit.py", "content": "# rate limiter\n"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_10", "content": "File written."},
                    ],
                },
                {"role": "user", "content": "Now write the full implementation with token bucket algorithm. Use the Write tool."},
            ],
        },
    },
    {
        "name": "T12-STREAM-PLAN: Streaming analysis",
        "expected": "PLANNING→BIG (streaming)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "stream": True,
            "messages": [
                {"role": "user", "content": "Analyze the security implications of storing API keys in environment variables vs a secrets manager. Compare AWS Secrets Manager, HashiCorp Vault, and .env files. Provide a recommendation with trade-offs for a small team."},
            ],
        },
    },
    # ── STRESS TEST: Complex analysis (exercises BIG_MODEL hard) ──
    {
        "name": "T13-PLAN: Deep codebase analysis (stress test)",
        "expected": "PLANNING→BIG (complex analysis)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8192,
            "tools": CC_TOOLS,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Lee exhaustivamente todos los archivos en vendor/claude-code-proxy/ — "
                        "el servidor, los transformers, el router, el compressor, streaming, utils, "
                        "y los tests. Después dame:\n\n"
                        "1. Un análisis arquitectónico del sistema (cómo fluye un request desde que "
                        "entra hasta que sale)\n"
                        "2. Identifica los 3 bugs más críticos que encuentres\n"
                        "3. Propón un fix concreto para el bug más grave (con código)"
                    ),
                },
            ],
        },
    },
    # ── CAPACITY TESTS (T14-T16) ── Verify model capabilities beyond routing ──
    {
        "name": "T14-PLAN: Multi-tool analysis (capacity stress)",
        "expected": "PLANNING→BIG (must use multiple tools)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8192,
            "tools": CC_TOOLS,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Read the file server.py, then grep for 'quality_score' in all files. "
                        "Tell me: 1) How many times quality_score appears, 2) In which files, "
                        "3) How it flows from scoring to metrics. Use the Read and Grep tools."
                    ),
                },
            ],
        },
    },
    {
        "name": "T15-EXECUTE: Multi-file edit (capacity stress)",
        "expected": "BUILDING→BUILDING_MODEL (must edit 2+ files)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t_cap1",
                            "name": "Write",
                            "input": {"file_path": "/app/utils.py", "content": "def helper(): pass"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t_cap1", "content": "Written."},
                    ],
                },
                {
                    "role": "user",
                    "content": (
                        "Now create a function add_numbers(a, b) in utils.py, "
                        "and import + call it from server.py. Edit both files."
                    ),
                },
            ],
        },
    },
    {
        "name": "T16-EXECUTE: Long context retention",
        "expected": "BUILDING→BUILDING_MODEL (context retention)",
        "body": {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "tools": CC_TOOLS,
            "messages": _build_long_context_messages(20),
        },
    },
]


def send_request(req):
    """Send a single request, return (success, latency_ms, response_summary)."""
    name = req["name"]
    body = req["body"]
    is_stream = body.get("stream", False)

    start = time.time()
    try:
        if is_stream:
            resp = requests.post(API, headers=HEADERS, json=body, stream=True, timeout=120)
            resp.raise_for_status()
            chunks = []
            text_parts = []
            tool_names = []
            for line in resp.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8", errors="replace")
                chunks.append(decoded)
                # Parse SSE data lines
                if decoded.startswith("data: "):
                    data_str = decoded[6:]
                    if data_str.strip() == "[DONE]":
                        continue
                    try:
                        evt = json.loads(data_str)
                        # Extract text deltas
                        delta = evt.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_parts.append(delta.get("text", ""))
                        # Extract tool use
                        cb = evt.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            tool_names.append(cb.get("name", "?"))
                    except json.JSONDecodeError:
                        pass

            latency = int((time.time() - start) * 1000)
            text = "".join(text_parts)
            summary = {
                "text_len": len(text),
                "text_preview": text[:150] + "..." if len(text) > 150 else text,
                "tool_calls": tool_names,
                "chunks": len(chunks),
                "stop_reason": "stream_end",
            }
            return True, latency, summary
        else:
            resp = requests.post(API, headers=HEADERS, json=body, timeout=120)
            latency = int((time.time() - start) * 1000)
            if resp.status_code != 200:
                return False, latency, {"error": resp.status_code, "detail": resp.text[:300]}
            data = resp.json()
            # Extract content
            content = data.get("content", [])
            text = ""
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append(block.get("name", "?"))
            summary = {
                "text_len": len(text),
                "text_preview": text[:150] + "..." if len(text) > 150 else text,
                "tool_calls": tool_calls,
                "stop_reason": data.get("stop_reason", "?"),
                "model": data.get("model", "?"),
                "usage": data.get("usage", {}),
            }
            return True, latency, summary
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return False, latency, {"error": str(e)}


def get_stats():
    try:
        return requests.get(STATS, timeout=5).json()
    except Exception as e:
        return {"error": str(e)}


def get_logs(n=50):
    try:
        return requests.get(f"{LOGS}?n={n}", timeout=5).json()
    except Exception as e:
        return {"error": str(e)}


def main():
    print(f"{'='*70}")
    print(f"  QUALITY VALIDATION — {len(REQUESTS)} requests to {BASE_URL}")
    print(f"{'='*70}\n")

    # Reset check: get initial stats
    pre_stats = get_stats()
    pre_requests = pre_stats.get("total_requests", 0)
    print(f"[pre] total_requests={pre_requests}\n")

    results = []
    for i, req in enumerate(REQUESTS, 1):
        name = req["name"]
        expected = req["expected"]
        is_stream = req["body"].get("stream", False)
        stream_tag = " [STREAM]" if is_stream else ""

        print(f"[{i:02d}/{len(REQUESTS)}] {name}{stream_tag}")
        print(f"        Expected: {expected}")

        ok, latency_ms, summary = send_request(req)
        results.append({"name": name, "ok": ok, "latency": latency_ms, "summary": summary})

        status = "OK" if ok else "FAIL"
        tool_str = f", tools={summary.get('tool_calls', [])}" if summary.get("tool_calls") else ""
        model_str = f", model={summary.get('model', '?')}" if summary.get("model") else ""
        text_len = summary.get("text_len", 0)

        print(f"        {status} ({latency_ms}ms) text={text_len} chars{tool_str}{model_str}")
        if not ok:
            print(f"        ERROR: {summary}")
        if summary.get("text_preview"):
            preview = summary["text_preview"].replace("\n", " ")[:100]
            print(f"        Preview: {preview}")
        print()

        # Small delay between requests to avoid overwhelming
        time.sleep(0.5)

    # ── Aggregate results ──
    print(f"\n{'='*70}")
    print("  RESULTS SUMMARY")
    print(f"{'='*70}\n")

    successes = sum(1 for r in results if r["ok"])
    failures = sum(1 for r in results if not r["ok"])
    avg_latency = sum(r["latency"] for r in results) // len(results) if results else 0

    print(f"Requests: {successes}/{len(results)} succeeded, {failures} failed")
    print(f"Avg latency: {avg_latency}ms")
    print()

    # ── Post-test stats ──
    print(f"{'='*70}")
    print("  PROXY METRICS (/api/stats)")
    print(f"{'='*70}\n")

    stats = get_stats()
    print(f"Total requests: {stats.get('total_requests', 0)} (+{stats.get('total_requests', 0) - pre_requests})")
    print(f"Total errors: {stats.get('total_errors', 0)}")
    print(f"Total fallbacks: {stats.get('total_fallbacks', 0)}")
    print(f"Fallback rate: {stats.get('fallback_rate_pct', 0):.1f}%")
    print()

    tq = stats.get("tool_quality", {})
    print("Tool Quality:")
    print(f"  native:       {tq.get('native', 0)}")
    print(f"  xml_extracted: {tq.get('xml_extracted', 0)}")
    print(f"  recovered:    {tq.get('recovered', 0)}")
    print(f"  truncated:    {tq.get('truncated', 0)}")
    print(f"  hallucinated: {tq.get('hallucinated', 0)}")
    print(f"  total:        {tq.get('total', 0)}")
    print(f"  success_rate: {tq.get('success_rate_pct', 0):.1f}%")
    print()

    clf = stats.get("classifier", {})
    print("Classifier:")
    print(f"  llm_success:    {clf.get('llm_success', 0)}")
    print(f"  regex_fallback: {clf.get('regex_fallback', 0)}")
    print(f"  disagreements:  {clf.get('disagreements', 0)}")
    print(f"  agreement_rate: {clf.get('agreement_rate_pct', 100):.1f}%")
    print()

    cost = stats.get("cost", {})
    print("Cost:")
    print(f"  total: ${cost.get('total_usd', 0):.6f}")
    print(f"  avg/request: ${cost.get('avg_per_request', 0):.6f}")
    by_model = cost.get("by_model", {})
    for m, c in sorted(by_model.items()):
        print(f"    {m}: ${c:.6f}")
    print()

    intents = stats.get("intents", {})
    print("Intents:")
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count}")
    print()

    qbp = stats.get("quality_by_phase", {})
    if qbp:
        print("Quality by Phase:")
        for phase, data in sorted(qbp.items()):
            print(f"  {phase}: count={data.get('count', 0)} avg_quality={data.get('avg_quality', 0):.2f}")
        print()

    mq = stats.get("model_quality", {})
    if mq:
        print("Model Quality Events:")
        for model, events in sorted(mq.items()):
            print(f"  {model}: {events}")
        print()

    # ── Per-request quality scores ──
    print(f"{'='*70}")
    print("  PER-REQUEST QUALITY SCORES (/api/logs)")
    print(f"{'='*70}\n")

    logs = get_logs(50)
    if isinstance(logs, list) and logs:
        scores = []
        print(f"{'Intent':<12} {'Model':<25} {'Score':>6} {'Latency':>8} {'Error'}")
        print("-" * 70)
        for log in logs[-15:]:  # Last 15 (our test run)
            intent = log.get("intent", "?")
            model = log.get("model_used", "?")
            score = log.get("quality_score", -1)
            latency = log.get("latency_ms", 0)
            error = log.get("error", "")
            scores.append(score)
            err_tag = f" ERR: {error[:40]}" if error else ""
            print(f"{intent:<12} {model:<25} {score:>6.2f} {latency:>7}ms{err_tag}")

        print()
        if scores:
            avg_score = sum(scores) / len(scores)
            min_score = min(scores)
            max_score = max(scores)
            low_quality = sum(1 for s in scores if s < 0.5)
            print(f"Quality Score Summary:")
            print(f"  avg:  {avg_score:.3f}")
            print(f"  min:  {min_score:.3f}")
            print(f"  max:  {max_score:.3f}")
            print(f"  <0.5: {low_quality}/{len(scores)} requests")
    else:
        print(f"No logs available: {logs}")

    print(f"\n{'='*70}")
    print("  DONE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
