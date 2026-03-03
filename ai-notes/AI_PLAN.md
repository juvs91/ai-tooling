# Quality Evaluation Loop for Analysis Requests

## Context
Claude Code proxy currently has a quality evaluation loop for analysis requests, but it only runs for **non-streaming** responses (see `server.py:262-321`). However, Claude Code client **always uses streaming** (`stream: true`). Therefore, the quality loop is dead code for actual usage.

## Problem Statement
We need to enable the quality evaluation loop for analysis requests that come via streaming. Two architectural approaches have been identified:

### Option E: Force Non-Streaming + Convert to SSE
1. **Approach**: Detect analysis requests (via `ctx.is_analysis` and `max_refinements > 0`), override `stream: false` in the request
2. **Execution**: Run the existing quality loop (which works with non-streaming)
3. **Conversion**: Convert the final refined Anthropic response to SSE events (Server-Sent Events) to match Claude Code's streaming expectation
4. **Pros**:
   - Reuses existing non-streaming quality loop
   - Clean separation: quality evaluation happens on complete response
   - No need to accumulate streaming chunks
5. **Cons**:
   - **3-5x latency penalty**: Must wait for full refinement cycle before sending first chunk
   - **Claude Code timeout risk**: Client expects first chunk within 30-60 seconds
   - **Event loop blocking**: `litellm.completion()` is **synchronous** for non-streaming (see `proxy.py:78`), which would block the event loop

### Option D: Post-Streaming Evaluation + Re-Request
1. **Approach**: Keep streaming enabled, accumulate full response text in a buffer
2. **Evaluation**: After stream completes, run quality evaluation on the accumulated response
3. **Re-request**: If quality score < threshold, initiate a new request (with feedback) and stream that as replacement
4. **Pros**:
   - No latency penalty for first attempt
   - Works with existing streaming UX (immediate first token)
   - Avoids blocking event loop
5. **Cons**:
   - Requires accumulating entire response (memory overhead)
   - Complex error handling if re-request fails
   - Potential duplicate content in chat history
   - Need to send "pings" during quality evaluation to keep connection alive

## Critical Issue Discovered
- **File**: `/Users/jeguzman/ai-tooling/vendor/claude-code-proxy/proxy/proxy.py:78`
- **Problem**: `litellm.completion()` is **synchronous** for non-streaming requests
- **Impact**: Makes Option E unsafe without async refactoring. Would block entire FastAPI event loop during quality refinements.

## Code Analysis
### Current Streaming Path (`server.py:235-256`)
```python
if is_stream:
    metrics.record(...)
    return StreamingResponse(
        handle_streaming(...),
        media_type="text/event-stream",
    )
```
- Returns `StreamingResponse` immediately
- Quality loop unreachable (inside non-streaming branch)

### Current Non-Streaming Path (`server.py:258-320`)
```python
anthropic_response = convert_litellm_to_anthropic(...)
max_refinements = cfg.analysis.max_refinements if ctx.is_analysis else 0
if max_refinements > 0:
    # Quality loop runs here
    ...
```
- Dead code for Claude Code client (always streams)

### Stream Handling (`streaming.py:500-656`)
- Complex XML tool parsing
- Native tool call handling
- JSON truncation repair
- No hooks for quality evaluation

## Decision: Option D (Post-Streaming Evaluation)
Given the critical blocking issue with `litellm.completion()` and the latency/timeout risks of Option E, we choose **Option D**.

### Implementation Plan
1. **Modify `server.py`**: Create `_analysis_quality_stream()` function that:
   - Accumulates stream chunks
   - Evaluates quality after stream completion
   - Sends periodic "ping" events during quality evaluation
   - Re-requests with feedback if needed

2. **Buffer Accumulation**: Use `_accumulate_stream()` helper to consume generator and reconstruct complete Anthropic response

3. **SSE Conversion**: Create `_response_to_sse_events()` helper to convert Anthropic response to SSE events

4. **Decision Point**: In `/v1/messages`, when `ctx.is_analysis` and `max_refinements > 0`, call `_analysis_quality_stream()` instead of regular `handle_streaming()`

### Key Components to Build
1. `_accumulate_stream(generator) -> tuple[AnthropicResponse, list[bytes]]`
   - Consumes the streaming generator
   - Returns the complete Anthropic response and raw chunks for replay

2. `_response_to_sse_events(response, request) -> Generator[bytes]`
   - Converts a complete Anthropic response to SSE events
   - Must match `handle_streaming()` output format exactly

3. `_analysis_quality_stream(generator, request, ctx, cfg) -> Generator[bytes]`
   - Main orchestrator: accumulate → evaluate → refine → stream
   - Sends "ping" events during LLM calls to keep connection alive

### Configuration Considerations
- `ANALYSIS_MODEL`: Optional override model for quality evaluation (could use cheaper/faster model)
- `ANALYSIS_MAX_REFINEMENTS`: Already exists (default 2)
- `ANALYSIS_QUALITY_THRESHOLD`: Could make configurable (currently hardcoded 0.75)

### Risks & Mitigations
1. **Memory overhead**: Buffer entire response. Mitigation: Limit to analysis requests only (not all streaming).
2. **Connection timeout**: Quality evaluation may take 10-30 seconds. Mitigation: Send periodic "ping" SSE events.
3. **Duplicate content**: Re-request adds to conversation history. Mitigation: Clear feedback after use? Or accept as learning.
4. **Error in re-request**: If second request fails, we've already sent first attempt. Mitigation: Return original stream with note about failed refinement.

## Timeline
1. **Phase 1 (Design)**: Write detailed plan (this document) ✅
2. **Phase 2 (Implementation)**: Build the three helper functions
3. **Phase 3 (Integration)**: Modify server.py to use new flow for analysis requests
4. **Phase 4 (Testing)**: Verify with logs, check ping events, validate quality loop triggers

## Success Metrics
- Analysis requests show `refinement_attempts > 0` in metrics
- Quality scores improve with refinements
- No connection timeouts during quality evaluation
- Streaming responses remain compatible with Claude Code client

## Next Steps
1. Implement `_accumulate_stream` helper
2. Implement `_response_to_sse_events` helper
3. Implement `_analysis_quality_stream` orchestrator
4. Modify `/v1/messages` endpoint to use new flow when `ctx.is_analysis and max_refinements > 0`
5. Test with analysis request and verify logs

## Notes
- The existing `_evaluate_analysis_quality` function (heuristic, no LLM) will be reused
- Need to ensure `ctx.refinement_attempt` and `ctx.quality_score` are tracked
- Must preserve existing metrics logging for analysis requests
