# Fire Test Analysis — GLM-4.7 via Passthrough

**Date:** 2026-03-01
**Prompt:** Spanish exhaustive analysis (T13-like)
**Model:** glm-4.7 via Z.AI Anthropic passthrough
**Turns:** 17 requests (16 PLANNING + 1 BUILDING)

---

## Quality Score: 35/100

---

## Proxy/Routing Analysis (PASS)

The proxy performed flawlessly from an infrastructure standpoint:

| Metric | Value | Verdict |
|--------|-------|---------|
| Routing accuracy | 17/17 correct | All turns routed to PLAN→glm-4.7 |
| Override 3 fired | 12/17 turns | BUILDING→PLANNING override worked |
| Passthrough usage | 15/17 via passthrough | Saved conversion overhead |
| Passthrough errors | 1 (HTTP 400 at turn 14) | Recovered via LiteLLM fallback |
| Classifier LLM | 17/17 success (0 regex fallback) | DeepSeek classifier stable |
| Tool quality | 100% (2 native, 0 hallucinated) | No tool issues |
| Total cost | $0.046 | Dominated by 1 LiteLLM fallback ($0.043) |

**Key observations:**
- Every turn sent **2 requests** (stream=true + stream=false pair) — CC's preflight pattern. This is normal CC behavior, not a proxy bug.
- Context grew steadily: 21K → 74K → 82K tokens across the session (no compression needed, well within 200K window).
- The HTTP 400 at turn 14 (74K tokens) may indicate Z.AI's Anthropic endpoint has a context limit lower than 200K, or a transient error.

---

## Conversation Flow Analysis

| Turn | Action | Tokens | Tool | Notes |
|------|--------|--------|------|-------|
| 1 | Initial prompt received | 21K | — | Classified PLANNING correctly |
| 2-3 | Read server.py, proxy.py, llm_router.py, model_mapper.py | 52K | Read x4 | Good file prioritization |
| 4-5 | Read compressor.py, streaming.py, converters.py, tool_prompting.py | 60K | Read x4 | Core modules |
| 6-7 | Read passthrough.py, pipeline.py, config.py, metrics.py, utils.py | 66K | Read x5 | Config + utilities |
| 8-9 | Glob transformers/*.py, Read 4 transformers | 67K | Glob+Read | Good discovery pattern |
| 10 | Glob tests/*.py, Read sse.py | 71K | Glob+Read | Listed tests but didn't read them |
| 11-12 | Read tool_prompting.py chunks (lines 2-401, 401-800, 801-1200) | 75K | Read x3 | Re-reading truncated file |
| 13 | **HTTP 400** — passthrough failed → LiteLLM fallback | 114K | — | 42s latency via fallback |
| 14-15 | Glob tests, Read tool_prompting.py remainder | 78K | Read | Continuing after recovery |
| 16 | **Write plan file** (392 lines) | 78K | Write | Wrote to plan file instead of responding |
| 17 | ExitPlanMode attempt → MiniMax | 82K | — | Stayed in plan mode |

---

## Response Quality Analysis (The Brutal Truth)

### Architecture Diagram: 60/100
- **Good:** The ASCII flow diagram is structurally correct. The pipeline order is right (Intent→Guardrail→TokenCap→Allowlist→ModelRouter→Passthrough→Convert→LiteLLM→Stream/Convert).
- **Good:** Component table maps files correctly.
- **Bad:** It's a regurgitation of what the code literally does, not an analysis. No insights about design tradeoffs, no commentary on why passthrough exists vs conversion, no evaluation of the pipeline pattern's strengths/weaknesses.
- **Bad:** Missed entirely: the dual-pipeline architecture (Phase 1 = Anthropic pipeline, Phase 2 = LiteLLM pipeline), the route override system for cross-provider routing, the quality scoring system (15 heuristics), the cost tracking system.

### Bug #1 — Circuit Breaker Race Condition: 10/100 (FABRICATED)
- **The claim:** "The check happens OUTSIDE the lock"
- **Reality:** Looking at [compressor.py:387-391](vendor/claude-code-proxy/llm/compressor.py#L387), the check IS inside `async with _state_lock`. The agent literally contradicts itself in the same analysis: "El código actual TIENE el lock en el lugar correcto (líneas 387-391)."
- **The real code:**
  ```python
  async with _state_lock:           # ← Lock ACQUIRED
      if _circuit_open_until > now:  # ← Check INSIDE lock
          return None                # ← Early exit INSIDE lock
  ```
- **The "additional race condition"** it describes (thundering herd between check and update) is not a bug — it's the **intended behavior** of every circuit breaker. You allow some requests through before tripping. This is correct.
- **Verdict:** Not a bug. The agent fabricated the severity and then admitted the code was correct in the same paragraph.

### Bug #2 — Memory Growing in Compression Cache: 5/100 (FABRICATED)
- **The claim:** "No size limit, can cause OOM"
- **Reality:** `_compression_cache` is a **single-entry cache** (one `Optional[_CompressionCache]` variable). It stores exactly ONE compressed summary at a time, with a 5-minute TTL. A compressed summary is typically 2-5KB.
- One variable holding one string for 5 minutes is not a memory leak. It cannot contribute to OOM in any realistic scenario.
- The proposed fix (adding a 10MB limit to a single-entry cache) is absurd — the entire compressed summary would never approach 10MB.
- **Verdict:** Completely fabricated bug. The agent didn't understand `Optional[X]` = single entry.

### Bug #3 — Timeout Inconsistency in LLM Recovery: 20/100 (WRONG FIX)
- **The observation:** litellm's timeout behavior varies by provider — this is actually true.
- **But the analysis is backwards:** `asyncio.wait_for()` is the CORRECT approach for hard timeouts because it cancels the coroutine regardless of provider behavior. The proposed fix (switching to litellm's `timeout` parameter) would actually be WORSE — it relies on each provider respecting the timeout, which is the exact problem the agent identified.
- **Verdict:** Correct observation, wrong conclusion, wrong fix.

---

## What the Agent MISSED (Real Issues)

| Missed Issue | Impact | Why It Matters |
|-------------|--------|----------------|
| **HTTP 400 during this session** | The passthrough got a 400 at 74K tokens — the agent was experiencing a real bug in real-time and didn't notice | Z.AI may have a lower limit than 200K for the Anthropic endpoint |
| **Dual-request pattern** | Every CC turn sends stream=true then stream=false — 2x the API calls | Cost and latency doubling |
| **Wrote to plan file instead of responding inline** | 392-line analysis went to a `.md` file, not displayed to user | CC plan mode behavioral quirk via proxy |
| **Stayed in plan mode** | The agent couldn't exit plan mode after writing | ExitPlanMode tool handling issue |
| **5-level regex complexity** | tool_prompting.py has 5 cascading regex levels — major maintenance risk | The agent read this file 3 times but didn't flag it |
| **No test coverage analysis** | Glob found 23 test files but agent never analyzed coverage gaps | Listed files without reading them |
| **Quality scoring system (15 heuristics)** | Completely missed utils/quality.py | A core subsystem went unanalyzed |

---

## Root Cause: Model Constraint vs Proxy Issue vs Architecture

### Model Constraint (PRIMARY CAUSE — 70% of failures)

**GLM-4.7 cannot do genuine critical analysis.** It can:
- Comprehend code structure (the architecture diagram is correct)
- Summarize what code does (accurate component table)
- Read and follow code flow

It cannot:
- Find real bugs (all 3 "bugs" were fabricated or exaggerated)
- Distinguish between design choices and actual defects
- Self-critique (contradicted itself about Bug #1 without noticing)
- Identify what's truly important vs. what looks impressive

This is a fundamental model capability gap. GLM-4.7 is a mid-tier model being asked to do a task that requires top-tier reasoning (bug finding, critical evaluation). Even Claude Opus struggles with this without verification.

### Architecture Issue (SECONDARY — 20% of failures)

1. **Plan mode trap**: CC entered plan mode and the agent wrote the analysis to a plan file instead of presenting it inline. This is a CC behavioral pattern that glm-4.7 doesn't know how to navigate. It tried to call ExitPlanMode but "stayed in plan mode" — the proxy may not handle ExitPlanMode correctly, or the model's tool call format was wrong.

2. **No verification loop**: The architecture has quality scoring (H1-H15) but it didn't catch fabricated bugs because the heuristics check for structural issues (empty response, truncated code), not for factual accuracy. There's no "verify your claims by checking the code" enforcement.

3. **Analysis guardrails inject tool enforcement** ("use tools to verify"), but glm-4.7 ignored this guidance and went straight to writing conclusions without re-reading the relevant code sections.

### Proxy Issue (MINOR — 10% of failures)

1. **HTTP 400 at 74K tokens**: The passthrough got a 400 from Z.AI at turn 14. This may indicate a context limit on Z.AI's Anthropic endpoint. The fallback to LiteLLM worked ($0.043 for that one request — 94% of total cost), but the error wasn't logged to the agent.

2. **Streaming latency reporting**: Several streaming requests show `latency_ms: 0` or `latency_ms: 12-14` which is the time-to-first-byte, not total request time. The actual streaming duration was much longer (visible in docker log timestamps: 30-40s between some turns). Metrics underreport streaming latency.

---

## What's Missing to Get to 80%

| Gap | Fix | Effort |
|-----|-----|--------|
| **Model can't find real bugs** | Use a stronger model for analysis (deepseek-reasoner, Claude Sonnet via real Anthropic) or add a verification step that forces the model to run tests / grep for the issues it claims | High |
| **No claim verification** | Add analysis guardrail: "Before reporting a bug, use Read to re-open the file and quote the exact problematic code. Run a test if possible." | Medium |
| **Plan mode trap** | Either disable plan mode for analysis requests, or teach the proxy to detect plan mode entry and inject "do NOT use plan mode, respond inline" into the system prompt | Medium |
| **Fabrication detection** | Add H16 heuristic to quality.py: if analysis claims N bugs but response contains no tool_use after initial exploration phase, penalize heavily (no verification = likely fabricated) | Low |
| **Stronger analysis model** | Route analysis to deepseek-reasoner (128K context, $2.19/M output but much better reasoning) or use the passthrough with a stronger model | Config change |
| **HTTP 400 investigation** | Test Z.AI Anthropic endpoint context limits with increasing payload sizes to find the actual ceiling | Low |

### Realistic path to 80%:
1. **Switch analysis model to deepseek-reasoner** — much better at critical reasoning
2. **Add verification guardrail** — force tool use before conclusions
3. **Fix plan mode behavior** — inject system prompt to prevent plan file writes
4. **Add fabrication detection heuristic** — penalize unverified claims

---

## Metrics Summary

```
Total requests:   17 (16 PLAN + 1 EXECUTE)
Total cost:       $0.046 ($0.043 from one LiteLLM fallback)
Avg latency:      4.4s (passthrough), 26.9s (LiteLLM fallback)
Input tokens:     1,100,269 (cumulative across all turns)
Output tokens:    6,796
Tool quality:     100% (2 native tools, 0 issues)
Classifier:       70.6% agreement (5 disagreements, all caught by Override 3)
Passthrough:      15/16 PLAN turns successful (1 HTTP 400 → fallback)
```
