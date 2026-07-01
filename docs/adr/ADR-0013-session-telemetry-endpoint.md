# ADR-0013: Session Telemetry Endpoint

**Status:** Accepted  
**Date:** 2026-07-01  
**Deciders:** jeguzman  

---

## Context

The proxy captures per-request metrics in a flat in-memory deque (`ProxyMetrics._logs`, maxlen=200) and exposes them via `/api/logs`. When debugging "what did a specific session do?" (e.g., reviewing Kimi's work), the only source of truth was the full Claude Code transcript. The proxy had no way to answer session-scoped questions from outside.

`ctx.session_id` already exists and flows through the entire pipeline (extracted from `X-Session-ID` header at `server.py:251`), and the concept is used for compression token budgets (`compressor.py`). The gap is that `session_id` is not recorded in `RequestLog` and not indexed.

## Decision

Extend the existing in-memory metrics system to index requests by `session_id`. Expose two new read-only endpoints:

- `GET /api/session/{session_id}/telemetry` — full timeline + summary for a session
- `GET /api/sessions` — list of recent sessions

All storage remains in-memory (same pattern as `ProxyMetrics._logs`). No new dependencies.

## Consequences

**Positive:**
- Session-level debugging is now possible via a single curl command
- Zero new dependencies or infrastructure
- `ctx.session_id` was already available — the wiring is minimal (~80 lines)
- Eviction piggybacks on the existing hourly cleanup

**Negative:**
- Data is lost on proxy restart (same as all current metrics)
- Max 100 requests per session in index (new sessions evict old entries via deque)
- Sessions older than 2h are evicted from index

## Alternatives Considered

- **SQLite persistence** — ruled out as overkill for in-session debugging; restart frequency is low
- **OpenTelemetry** — too heavy; requires external collector infrastructure
- **Expose session_state.py data** — already exists but scoped to entity/decision tracking, not request-level metrics

## Implementation

Changes to 3 files:
1. `utils/metrics.py` — `session_id` field in `RequestLog`, `_session_index` dict, `get_session_telemetry()`, `get_sessions_summary()`, `evict_old_sessions()`
2. `server.py` — pass `ctx.session_id` to `RequestLog` at both recording points; add 2 new routes
3. `llm/compressor.py` — call `metrics.evict_old_sessions()` in hourly cleanup
