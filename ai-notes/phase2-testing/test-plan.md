# Phase 2 End-to-End Testing Plan

## Test Configuration
- Proxy URL: http://127.0.0.1:8083
- Router: mixed-router (glm-4.7 for PLANNING, MiniMax-M2.5 for BUILDING, deepseek-chat for CHAT)
- Compression threshold: 20 messages
- Max turns: 1000
- Test date: 2026-03-08

## Test Scenarios

### 1. Complex Multi-File Analysis (30+ turns)
**Goal**: Verify model routing and compression under sustained analysis work
- Read 10+ Python files in vendor/claude-code-proxy/
- Focus on: compressor.py, config.py, proxy.py, transformers/
- Expected: Use glm-4.7 for PLANNING tasks
- Monitor: Compression triggers at 20+ messages
- Checkpoint: Capture stats every 10 turns

**Metrics to collect**:
- Model routing distribution (should favor glm-4.7)
- Compression trigger frequency
- Token count trajectory
- Cache hit rate evolution
- Error rate (target: 0%)

### 2. Building Task with Multiple Edits (20+ turns)
**Goal**: Test BUILDING model routing and tool inflation detection
- Refactor compressor.py component
- Perform multiple WRITE operations
- Expected: Use MiniMax-M2.5 for BUILDING
- Monitor: Tool inflation detection
- Checkpoint: Capture stats every 5 turns

**Metrics to collect**:
- Model routing (should use MiniMax-M2.5)
- Tool inflation detection rate
- Dynamic limit enforcement
- Write operation success rate

### 3. Long Session (50+ turns)
**Goal**: Verify sustained operation without degradation
- Cycle: analyze → edit → test → repeat
- Expected: Reach 50+ turns without 429 errors
- Monitor: Compression happens repeatedly
- Monitor: Cache hit rate improves
- Monitor: Quality doesn't degrade
- Checkpoint: Capture stats every 10 turns

**Metrics to collect**:
- Turn completion rate (target: 100%)
- Compression frequency
- Cache hit rate trajectory
- Quality degradation metrics
- Session limit hits (target: 0)

### 4. Mixed Routing Verification
**Goal**: Validate intent classification accuracy
- Test PLANNING tasks → should use glm-4.7
- Test BUILDING tasks → should use MiniMax-M2.5
- Test CHAT tasks → should use deepseek-chat
- Capture stats for each task type

**Metrics to collect**:
- Intent classification accuracy
- Model routing correctness
- Task type distribution

## Data Collection Protocol

### Real-Time Monitoring Commands
```bash
# Stats API (run every checkpoint)
curl -s http://127.0.0.1:8083/api/stats | jq .

# Logs for compression and routing
docker logs ai-tooling-proxy_cloud-1 -f | grep -E "\[compress\]|\[route\]"

# Proxy health
curl -s http://127.0.0.1:8083/health | jq .
```

### Checkpoint Data Structure
```json
{
  "checkpoint": 1,
  "timestamp": "2026-03-08T10:00:00Z",
  "turn_count": 10,
  "stats": {
    "total_requests": 10,
    "total_errors": 0,
    "compression_effectiveness": {...},
    "cache": {...},
    "providers": {...},
    "intents": {...}
  },
  "observations": "Compression triggered at turn 8, used glm-4.7"
}
```

## Success Criteria
- ✅ Session reaches 50+ turns without 429 errors
- ✅ Compression triggers at 20+ messages consistently
- ✅ Multi-model routing works correctly (PLANNING→glm-4.7, BUILDING→MiniMax-M2.5, CHAT→deepseek-chat)
- ✅ Cache hit rate improves over time (target: >30%)
- ✅ Quality remains acceptable after compression (subjective assessment)
- ✅ No session limits hit

## Deliverables
1. Test results with stats at each checkpoint
2. Compression effectiveness analysis
3. Model routing verification report
4. Performance impact assessment
5. Quality degradation analysis
6. Issues and anomalies log
