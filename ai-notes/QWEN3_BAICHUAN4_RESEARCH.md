# Qwen 3 & Baichuan 4 Research for Autonomous Coding Agents

**Date**: 2026-02-28
**Benchmark baseline**: GLM-4.7 ($0.38/M input, 87.4% tau2-Bench, 73.8% SWE-bench Verified, 90.6% tool-calling success)

---

## QWEN 3 MODEL FAMILY

### Complete Variant List with Pricing

| Model | Total Params | Active Params | Context | Input $/M | Output $/M | Release |
|-------|-------------|---------------|---------|-----------|------------|---------|
| **Qwen3.5-397B-A17B** (flagship) | 397B | 17B | 1M | $0.40-$0.60 | $2.40-$3.60 | Feb 16, 2026 |
| **Qwen3.5-Plus** (hosted 397B) | 397B | 17B | 1M | $0.10 | $0.40 | Feb 15, 2026 |
| **Qwen3.5-Flash** | ? | ? | 262K | $0.40 | $3.20 | Feb 24, 2026 |
| **Qwen3.5-122B-A10B** | 122B | 10B | ? | ~$0.10 | ~$0.40 | Feb 24, 2026 |
| **Qwen3.5-35B-A3B** | 35B | 3B | ? | ~$0.10 | ~$0.40 | Feb 24, 2026 |
| **Qwen3.5-27B** | 27B | 27B (dense) | ? | ~$0.10 | ~$0.40 | Feb 24, 2026 |
| **Qwen3-Max** | undisclosed | undisclosed | 262K | $1.20 | $6.00 | Sep 23, 2025 |
| **Qwen3-Max-Thinking** | undisclosed | undisclosed | 262K | $0.55 | $3.50 | 2025 |
| **Qwen3-235B-A22B** | 235B | 22B | 262K | $0.20-$0.70 | $1.00-$6.00 | 2025 |
| **Qwen3-Coder 480B-A35B** | 480B | 35B | 256K (1M extrap) | $0.22 | $1.00 | Jul 23, 2025 |
| **Qwen3-Coder-Next** | 80B | 3B | 256K | $0.07-$1.20 | $0.30-$6.00 | Feb 4, 2026 |
| **Qwen-Plus** | ? | ? | 131K | ~$0.20 | ~$1.00 | ongoing |
| **Qwen-Turbo** | ? | ? | 131K | $0.05 | $0.20 | deprecated |

**Notes on pricing variance**: OpenRouter vs Alibaba Cloud direct vs third-party providers differ significantly. Alibaba Cloud direct tends to be cheapest. OpenRouter pricing fluctuates by provider tier.

### Coding Benchmarks

| Model | SWE-bench Verified | SWE-bench Pro | SWE-bench Multi | LiveCodeBench v6 | HumanEval |
|-------|-------------------|---------------|-----------------|-------------------|-----------|
| **GLM-4.7** (baseline) | **73.8%** | 40.6% | 63.7% | -- | -- |
| **Qwen3.5-397B-A17B** | **76.4%** | -- | -- | **83.6** | 79.3% |
| **Qwen3-Coder 480B** | 66.5-69.6% | -- | -- | -- | -- |
| **Qwen3-Coder-Next** | **70.6%** | **44.3%** | 62.8% | **74.5** | **94.1%** |
| **Qwen3-235B-A22B** | -- | -- | -- | 74.8 | -- |
| **Qwen3-Max** | -- | -- | -- | -- | 92.7% |

### Agent / Tool Calling Benchmarks

| Model | tau2-Bench | BFCL v3 (simple) | BFCL v3 (multiple) | BFCL v3 (parallel) | Tool-Call Success |
|-------|-----------|-------------------|---------------------|---------------------|-------------------|
| **GLM-4.7** (baseline) | **87.4%** | -- | -- | -- | **90.6%** |
| **Qwen3.5-397B-A17B / Plus** | **86.7%** | -- | -- | -- | -- |
| **Qwen3-Coder-Plus** | -- | 81.7% | 80.9% | 37.5% | -- |
| **Qwen3-235B-A22B** | ~69.6% | 70.8 (overall) | -- | -- | -- |

**KEY FINDING**: Qwen3's parallel function calling is weak (37.5% on BFCL v3 parallel). Simple/multiple calling is decent (~81%). This is a significant concern for autonomous coding agents that chain tool calls.

### Function Calling Support

- **Native support**: Yes, Qwen3-Coder and Qwen3.5 have native function calling / tool use
- **Reliability**: Strong for simple sequential calls (~82%), weak for parallel calls (~38%)
- **Known issues**: BFCL v3 parallel and parallel_multiple scores are low; models struggle with invoking multiple functions simultaneously
- **Format**: Supports OpenAI-compatible function calling format
- **Agent scaffolding**: Qwen3-Coder-Next specifically trained for agent scaffolds (Claude Code, Cline, Trae, etc.)

### API Availability

| Provider | Models Available | Notes |
|----------|-----------------|-------|
| **Alibaba Cloud / Qwen API** | All variants | Cheapest pricing, direct API |
| **OpenRouter** | Qwen3-Max, Qwen3-Coder 480B (free tier!), Qwen3-Coder-Next, Qwen3.5-Plus, Qwen3.5-397B | Full availability |
| **Z.AI** | Unknown | Not confirmed; Z.AI primarily serves GLM models |
| **DeepInfra** | Multiple Qwen3 variants | Competitive pricing |
| **Together AI** | Qwen3-Coder 480B | Confirmed available |
| **Nebius** | Qwen3-Coder 480B | Confirmed available |
| **Cerebras** | Qwen3-Coder 480B | High-speed inference |
| **NVIDIA NIM** | Qwen3.5-397B | Enterprise deployment |
| **Ollama** | Qwen3-Coder-Next | Local deployment (3B active fits consumer GPUs) |

### Top Qwen Candidates for Our Proxy

**1. Qwen3.5-Plus (hosted)** -- BEST OVERALL VALUE
- tau2-Bench: 86.7% (vs GLM-4.7's 87.4% -- nearly identical)
- SWE-bench: 76.4% (BEATS GLM-4.7's 73.8%)
- Price: $0.10 input / $0.40 output (vs GLM-4.7's $0.38 input)
- Context: 1M tokens (vs GLM-4.7's 128K)
- Built-in tools and adaptive tool use
- Available on OpenRouter

**2. Qwen3-Coder-Next** -- BEST FOR LOCAL / COST-SENSITIVE
- SWE-bench: 70.6% (decent, 3% below GLM-4.7)
- SWE-bench Pro: 44.3% (BEATS GLM-4.7's 40.6%)
- Only 3B active params = extremely cheap inference
- 256K context
- Specifically trained for coding agent scaffolds
- Runs locally on consumer hardware via Ollama

**3. Qwen3-Coder 480B** -- STRONG BUT EXPENSIVE
- SWE-bench: 66.5-69.6% (below GLM-4.7)
- Large model, 35B active
- $0.22 input / $1.00 output
- Free tier on OpenRouter (!)

---

## BAICHUAN 4 MODEL FAMILY

### Overview

Baichuan 4 was released May 22, 2024 by Baichuan Intelligence. It is the company's latest generation base model.

### Available Variants

| Model | Context | Parameters | Notes |
|-------|---------|------------|-------|
| **Baichuan 4** | Unknown (likely 128K based on Baichuan2-Turbo-128K precedent) | Undisclosed (proprietary) | Flagship model |
| **Baichuan3-Turbo** | Standard | Undisclosed | API available |
| **Baichuan3-Turbo-128k** | 128K | Undisclosed | Long-context variant |
| **Baichuan-M1-14B** | ? | 14B | Open-weight, medical focus |
| **Baichuan-M2-32B** | ? | 32B | Medical reasoning, built on Qwen2.5-32B |

### Coding Benchmarks

**NO PUBLISHED SWE-bench, HumanEval, LiveCodeBench, or BFCL scores found.**

Baichuan claims:
- General capabilities increased >10% over Baichuan 3
- Math ability +14%
- Coding ability +9%
- "Ahead of Gemini Pro and Claude 3 Sonnet" (May 2024 comparison -- outdated)

But these are self-reported relative improvements with no absolute numbers on standard coding benchmarks.

### Agent / Tool Calling Benchmarks

**NO tau2-Bench, BFCL, TAU-bench, or agent benchmark scores found.**

### Function Calling Support

- **API format**: OpenAI-compatible (confirmed via Zenlayer docs)
- **Native function calling**: Unclear; no benchmark data on tool calling reliability
- **Known issues**: No data available

### API Availability

| Provider | Available? | Notes |
|----------|-----------|-------|
| **Baichuan Direct API** | Yes | Four APIs open for developers |
| **OpenRouter** | **NO** | Not listed on OpenRouter as of Feb 2026 |
| **Z.AI** | **NO** | Z.AI serves GLM models only |
| **Zenlayer/TheTurbo** | Yes | API documentation available |
| **International access** | Limited | Primarily China-domestic focused |

### Pricing

**No specific per-token pricing found.** Baichuan participated in the 2024 Chinese LLM price war but did not publish transparent per-token rates in the way Alibaba/Qwen does.

### Assessment

**Baichuan 4 is NOT a viable candidate for our proxy** for the following reasons:
1. No presence on OpenRouter or Z.AI
2. No published coding benchmarks (SWE-bench, HumanEval, etc.)
3. No published agent/tool-calling benchmarks
4. No transparent pricing
5. Limited international API access
6. Model released May 2024 -- nearly 2 years old with no Baichuan 5 successor
7. Company has pivoted to domain-specific models (medical: M1, M2) rather than general/coding
8. The medical models (M1-14B, M2-32B) are built on Qwen2.5-32B base, suggesting Baichuan lacks a competitive foundation model

---

## COMPARATIVE SUMMARY vs GLM-4.7 BASELINE

| Metric | GLM-4.7 | Qwen3.5-Plus | Qwen3-Coder-Next | Baichuan 4 |
|--------|---------|--------------|-------------------|------------|
| **SWE-bench Verified** | 73.8% | **76.4%** | 70.6% | Unknown |
| **SWE-bench Pro** | 40.6% | -- | **44.3%** | Unknown |
| **tau2-Bench** | **87.4%** | 86.7% | -- | Unknown |
| **BFCL v3 (overall)** | -- | -- | ~81% simple | Unknown |
| **Tool-call success** | **90.6%** | -- | -- | Unknown |
| **HumanEval** | -- | 79.3% | **94.1%** | Unknown |
| **LiveCodeBench v6** | -- | **83.6** | 74.5 | Unknown |
| **Context window** | 128K | **1M** | 256K | ~128K |
| **Input $/M** | $0.38 | **$0.10** | **$0.07** | Unknown |
| **Output $/M** | -- | $0.40 | $0.30 | Unknown |
| **OpenRouter** | Yes (Z.AI) | Yes | Yes | **No** |
| **Function calling** | Native, reliable | Native, good | Native, agent-trained | Unknown |

---

## RECOMMENDATIONS

### For proxy integration (ranked by viability):

**1. Qwen3.5-Plus** -- Strongest overall candidate
- Matches or beats GLM-4.7 on nearly every metric
- 76.4% SWE-bench (vs 73.8% GLM-4.7)
- 86.7% tau2-Bench (vs 87.4% GLM-4.7 -- within margin)
- $0.10/M input = 73% cheaper than GLM-4.7
- 1M context window (8x GLM-4.7)
- Available on OpenRouter
- RISK: Parallel function calling weakness (Qwen family trait)

**2. Qwen3-Coder-Next** -- Best for local/cost-sensitive deployment
- Designed specifically for coding agents
- 94.1% HumanEval, 70.6% SWE-bench
- Only 3B active = runs on consumer hardware
- $0.07/M input on Alibaba Cloud
- RISK: Lower SWE-bench than GLM-4.7; parallel tool calling weakness

**3. Qwen3-Coder 480B** -- Free tier option
- Available free on OpenRouter
- Good for testing/fallback
- SWE-bench 66-70% is below GLM-4.7
- RISK: Lower coding scores; expensive if not on free tier

**4. Baichuan 4** -- NOT RECOMMENDED
- No benchmark data, no OpenRouter, no transparent pricing
- Not competitive for coding agent use case
- Company pivoting away from general-purpose models

### Key Risk: Qwen Parallel Function Calling

The BFCL v3 data shows Qwen models scoring only 37.5% on parallel function calls and 41.7% on parallel_multiple. For an autonomous coding agent that needs to chain tool calls (read file + grep + edit), this is a real concern. GLM-4.7's 90.6% tool-calling success rate remains the benchmark to beat.

**Mitigation**: Our proxy already handles tool call XML extraction and fallback. Qwen3-Coder-Next is specifically trained for agent scaffolds and tool recovery, which may partially compensate for raw parallel calling weakness.
