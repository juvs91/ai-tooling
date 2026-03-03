#!/bin/bash
# Captures metrics, logs, and docker logs after a fire test session
# Usage: ./scripts/fire-test-capture.sh [optional-label]

LABEL="${1:-fire-test}"
DEST="ai-notes/${LABEL}-$(date +%Y%m%d-%H%M%S)"
PROXY_URL="${PROXY_URL:-http://localhost:8083}"
CONTAINER="${CONTAINER:-ai-tooling-proxy_cloud-1}"

mkdir -p "$DEST"

echo "Capturing from $PROXY_URL (container: $CONTAINER)..."

# 1. Aggregate metrics
curl -s "$PROXY_URL/api/stats" | python3 -m json.tool > "$DEST/stats.json" 2>/dev/null

# 2. Per-request logs (all — max 200)
curl -s "$PROXY_URL/api/logs?n=200" | python3 -m json.tool > "$DEST/logs.json" 2>/dev/null

# 3. Docker logs (full + filtered views)
docker logs "$CONTAINER" 2>&1 > "$DEST/docker-full.log"
grep -E "\[classify\]" "$DEST/docker-full.log" > "$DEST/classifier.log" 2>/dev/null
grep -E "\[passthrough\]" "$DEST/docker-full.log" > "$DEST/passthrough.log" 2>/dev/null
grep -E "OVERRIDE|DISAGREE|ANALYSIS" "$DEST/docker-full.log" > "$DEST/overrides.log" 2>/dev/null
grep -E "\[compress\]|\[pipeline\]" "$DEST/docker-full.log" > "$DEST/pipeline.log" 2>/dev/null

echo ""
echo "=== Captured to: $DEST ==="
ls -lh "$DEST/"
echo ""
echo "=== Quick Summary ==="
python3 -c "
import json, sys
try:
    s = json.load(open('$DEST/stats.json'))
    l = json.load(open('$DEST/logs.json'))
except Exception as e:
    print(f'Error reading captures: {e}')
    sys.exit(1)

print(f'Requests: {s[\"total_requests\"]}  Errors: {s[\"total_errors\"]}  Fallbacks: {s[\"total_fallbacks\"]}')
print(f'Intents: {s.get(\"intents\", {})}')
cost = s.get('cost', {})
print(f'Cost: \${cost.get(\"total_usd\", 0):.4f}  (avg: \${cost.get(\"avg_per_request\", 0):.4f}/req)')
if cost.get('by_model'):
    for m, c in cost['by_model'].items():
        print(f'  {m}: \${c:.4f}')
cls = s.get('classifier', {})
print(f'Classifier: LLM={cls.get(\"llm_success\",0)} regex_fb={cls.get(\"regex_fallback\",0)} disagree={cls.get(\"disagreements\",0)} agree={cls.get(\"agreement_rate_pct\",0):.1f}%')
tq = s.get('tool_quality', {})
print(f'Tool quality: native={tq.get(\"native\",0)} xml={tq.get(\"xml_extracted\",0)} recovered={tq.get(\"recovered\",0)} truncated={tq.get(\"truncated\",0)} hallucinated={tq.get(\"hallucinated\",0)} rate={tq.get(\"success_rate_pct\",0):.1f}%')

print(f'\nPer-request logs: {len(l)} entries')
print(f'{\"Intent\":<12} {\"Model\":<28} {\"Provider\":<14} {\"Score\":>6} {\"Latency\":>8} {\"Tokens\":>8}')
print('-' * 80)
for r in l:
    model = r.get('model_used','?')
    if len(model) > 27: model = '...' + model[-24:]
    tok = r.get('input_tokens',0) + r.get('output_tokens',0)
    print(f'{r.get(\"intent\",\"?\"):<12} {model:<28} {r.get(\"provider\",\"?\"):<14} {r.get(\"quality_score\",0):>5.2f} {r.get(\"latency_ms\",0):>7}ms {tok:>7}')
"
