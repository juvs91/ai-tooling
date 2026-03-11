# End-to-End Test Plan: Proxy Transformers Validation

**Date**: 2026-03-09
**Purpose**: Validate that AGNOSTIC transformers work correctly in the proxy
**Priority**: P0 - CRITICAL (verification of P0 implementation)

## Test Scenarios

### Test 1: XML Tool Extraction from Text Content

**Request**: Send a request where model returns text with embedded XML tool calls

**Expected Behavior**:
- UniversalToolExtractionTransformer extracts tools from text
- strip_tool_call_xml cleans orphaned XML tags from remaining text
- User sees clean text without XML artifacts
- Tools are executed

**Test Command**:
```bash
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.6",
    "max_tokens": 1000,
    "messages": [
      {
        "role": "user",
        "content": "Tell me the current directory contents"
      }
    ],
    "tools": [
      {
        "name": "Bash",
        "input_schema": {
          "type": "object",
          "properties": {
            "command": {
              "type": "string"
            }
          },
          "required": ["command"]
        }
      }
    ]
  }' | jq .
```

**Verification**:
- Check proxy logs: `[universal-tool-extraction] Extracted X tool(s) from text content`
- Check proxy logs: `[universal-tool-extraction] Cleaned orphaned XML tags from remaining text`
- Verify response contains tool_use blocks (not XML in text)

---

### Test 2: Reasoning Content Extraction

**Request**: Send a request where model returns `<reasoning>` tags with embedded tools

**Expected Behavior**:
- ReasoningHandlingTransformer strips reasoning tags
- Extracts tools from reasoning content
- strip_tool_call_xml cleans orphaned XML tags from remaining reasoning
- Tools are executed

**Test Command**:
```bash
# Test with a model that supports reasoning (e.g., deepseek-reasoner)
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek/deepseek-reasoner",
    "max_tokens": 1000,
    "messages": [
      {
        "role": "user",
        "content": "Analyze this file and tell me what it does"
      }
    ]
  }' | jq .
```

**Verification**:
- Check proxy logs: `[reasoning-handling] Extracted X tool(s) from reasoning content`
- Check proxy logs: `[reasoning-handling] Cleaned orphaned XML tags from reasoning content`
- Verify reasoning_content is cleaned

---

### Test 3: Mixed Response Handling

**Request**: Send a request where model returns thinking + content + tools

**Expected Behavior**:
- UniversalToolExtractionTransformer extracts tools from all sources
- No duplicate tools extracted
- strip_tool_call_xml cleans all text content
- All tools are executed in correct order

**Test Command**:
```bash
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.6",
    "max_tokens": 1000,
    "messages": [
      {
        "role": "user",
        "content": "Read a file and then write a new file with modified content"
      }
    ],
    "tools": [
      {
        "name": "Read",
        "input_schema": {
          "type": "object",
          "properties": {
            "file_path": {
              "type": "string"
            }
          },
          "required": ["file_path"]
        }
      },
      {
        "name": "Write",
        "input_schema": {
          "type": "object",
          "properties": {
            "file_path": {
              "type": "string"
            },
            "content": {
              "type": "string"
            }
          },
          "required": ["file_path", "content"]
        }
      }
    ]
  }' | jq .
```

**Verification**:
- Check proxy logs: `[universal-tool-extraction] Processing mixed response`
- Verify both Read and Write tools are extracted
- Verify no duplicate tools

---

### Test 4: Orphaned XML Tag Cleanup

**Request**: Send a request that triggers orphaned XML tags

**Expected Behavior**:
- strip_tool_call_xml removes all orphaned XML tags
- User sees clean text without XML artifacts
- Response quality is professional

**Test Command**:
```bash
# Use a model known to have XML fragments
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm/glm-4.7",
    "max_tokens": 1000,
    "messages": [
      {
        "role": "user",
        "content": "List the files in the current directory"
      }
    ],
    "tools": [
      {
        "name": "Glob",
        "input_schema": {
          "type": "object",
          "properties": {
            "pattern": {
              "type": "string"
            }
          },
          "required": ["pattern"]
        }
      }
    ]
  }' | jq .
```

**Verification**:
- Check proxy logs for cleanup messages
- Check response text contains NO orphaned XML tags
- Verify text is clean and readable

---

### Test 5: Streaming Response Handling

**Request**: Send a streaming request to verify transformers work with streaming

**Expected Behavior**:
- Transformers process stream chunks correctly
- Tools are extracted from stream
- strip_tool_call_xml cleans text content in stream
- No orphaned XML tags in streamed text

**Test Command**:
```bash
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "model": "claude-sonnet-4.6",
    "max_tokens": 1000,
    "stream": true,
    "messages": [
      {
        "role": "user",
        "content": "Tell me about this file"
      }
    ],
    "tools": [
      {
        "name": "Read",
        "input_schema": {
          "type": "object",
          "properties": {
            "file_path": {
              "type": "string"
            }
          },
          "required": ["file_path"]
          }
        }
      }
    ]
  }'
```

**Verification**:
- Check proxy logs: Stream processing messages
- Verify tools are extracted from stream
- Verify no orphaned XML tags in streamed text

---

## Log Analysis Commands

### Check Universal Tool Extraction Logs
```bash
curl -s "http://127.0.0.1:8083/api/logs?n=50" | jq '.[] | select(.log | contains("universal-tool-extraction")) | {timestamp, log}'
```

### Check Reasoning Handling Logs
```bash
curl -s "http://127.0.0.1:8083/api/logs?n=50" | jq '.[] | select(.log | contains("reasoning-handling")) | {timestamp, log}'
```

### Check strip_tool_call_xml Cleanup Logs
```bash
curl -s "http://127.0.0.1:8083/api/logs?n=50" | jq '.[] | select(.log | contains("Cleaned orphaned XML tags")) | {timestamp, log}'
```

### Check Tool Extraction Metrics
```bash
curl -s "http://127.0.0.1:8083/api/stats" | jq '.tool_extraction'
```

---

## Success Criteria

### Must Pass (Blocking)
- ✅ Test 1: XML tools extracted from text content
- ✅ Test 2: Tools extracted from reasoning content
- ✅ Test 3: Mixed responses handled correctly (no duplicates)
- ✅ Test 4: Orphaned XML tags cleaned from text
- ✅ Test 5: Streaming responses processed correctly

### Should Pass (Important)
- ✅ No orphaned XML tags in user-facing text
- ✅ All tool extraction transformers log correctly
- ✅ strip_tool_call_xml cleanup logs appear when needed
- ✅ Hot-reload works after code changes

### Nice to Have (Optional)
- ✅ Tool extraction rate >95%
- ✅ Response quality improved (professional, no XML artifacts)
- ✅ No performance regression (<100ms overhead)

---

## Test Execution Plan

### Phase 1: Prepare Environment
1. Start proxy if not running: `docker-compose up -d cloud`
2. Verify proxy health: `curl -s http://127.0.0.1:8083/health | jq .`
3. Clear logs: `curl -X POST http://127.0.0.1:8083/api/logs/clear`

### Phase 2: Execute Tests
1. Run Test 1 (XML Tool Extraction)
2. Analyze logs and verify success
3. Run Test 2 (Reasoning Content Extraction)
4. Analyze logs and verify success
5. Run Test 3 (Mixed Response Handling)
6. Analyze logs and verify success
7. Run Test 4 (Orphaned XML Tag Cleanup)
8. Analyze logs and verify success
9. Run Test 5 (Streaming Response Handling)
10. Analyze logs and verify success

### Phase 3: Document Results
1. Record test results in ai-notes/
2. Document any failures or issues
3. Update IMPLEMENTATION_SUMMARY.md with test results
4. If all tests pass: Mark P0 as PRODUCTION-VERIFIED

---

## Expected Log Output Examples

### Successful XML Tool Extraction
```
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Processing text content (150 chars)
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Extracted 1 tool(s) from text content
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Cleaned orphaned XML tags from remaining text (150 -> 85 chars)
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Updated text block with cleaned content (1 blocks updated)
```

### Successful Reasoning Content Extraction
```
INFO:llm.transformers.reasoning_handling:[reasoning-handling] Extracted 1 tool(s) from reasoning content (200 chars)
INFO:llm.transformers.reasoning_handling:[reasoning-handling] Cleaned orphaned XML tags from reasoning content (200 -> 120 chars)
```

### Successful Mixed Response Handling
```
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Processing mixed response
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Extracted 2 tool(s) from response (thinking: 1, content: 0, tool_use: 1)
INFO:llm.transformers.universal_tool_extraction:[universal-tool-extraction] Deduplicated to 2 unique tool calls
```

---

## Troubleshooting

### If Tests Fail

**No tools extracted**:
- Check proxy logs for errors
- Verify transformers are registered in `__init__.py`
- Verify response pipeline is integrated in `server.py`

**Orphaned XML tags still visible**:
- Check if `strip_tool_call_xml` is being called
- Verify the import is correct in transformers
- Check logs for cleanup messages

**Streaming not working**:
- Verify streaming endpoint is enabled
- Check if response pipeline runs before streaming starts
- Look for SSE event processing errors

**Hot-reload not working**:
- Verify uvicorn `--reload` flag is set
- Check if Docker volume mounts are correct
- Restart proxy container if needed

---

## Conclusion

**Test Status**: ⏳ PENDING EXECUTION

**Next Steps**:
1. Execute all 5 test scenarios
2. Analyze logs and verify success criteria
3. Document results
4. If successful: P0 CRITICAL FIX = PRODUCTION-VERIFIED ✅

---

**Test Plan Created**: 2026-03-09
**Test Execution**: PENDING
**Status**: Ready for execution