#!/bin/bash
# fire-test.sh - Prueba de fuego para claude-code-proxy
# Usage: ./scripts/fire-test.sh [label]

set -e

LABEL="${1:-fire-test-$(date +%Y%m%d-%H%M%S)}"
DEST="ai-notes/${LABEL}"
PROXY_URL="${PROXY_URL:-http://localhost:8085}"
CONTAINER="ai-tooling-proxy_test-1"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

mkdir -p "$DEST"
echo -e "${GREEN}=== FIRE TEST: $LABEL ===${NC}"
echo "Destino: $DEST"
echo "Proxy: $PROXY_URL"
echo "Container: $CONTAINER"
echo ""

# Health check
echo -e "${YELLOW}[1/7] Health check...${NC}"
HEALTH=$(curl -s "$PROXY_URL/health" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','error'))" 2>/dev/null || echo "error")
if [ "$HEALTH" != "healthy" ]; then
    echo -e "${RED}ERROR: Proxy no saludable (status: $HEALTH)${NC}"
    echo "Inicia el test proxy:"
    echo "  docker compose -f docker-compose.yml -f cloud-provider-ymls/docker-compose.test.override.yml up -d proxy_test"
    exit 1
fi
echo -e "${GREEN}Proxy saludable ✅${NC}"
echo ""

# Baseline metrics
echo -e "${YELLOW}[2/7] Capturando baseline...${NC}"
curl -s "$PROXY_URL/api/stats" 2>/dev/null | python3 -m json.tool > "$DEST/baseline.json" || echo "{}" > "$DEST/baseline.json"
BASELINE_REQS=$(python3 -c "import json; d=json.load(open('$DEST/baseline.json')); print(d.get('total_requests',0))" 2>/dev/null || echo "0")
BASELINE_COST=$(python3 -c "import json; d=json.load(open('$DEST/baseline.json')); print(d.get('cost',{}).get('total_usd',0))" 2>/dev/null || echo "0")
echo "Baseline: $BASELINE_REQS requests, \$$BASELINE_COST"
echo ""

# Iniciar log capture
echo -e "${YELLOW}[3/7] Iniciando captura de logs...${NC}"
docker logs "$CONTAINER" -f > "$DEST/docker.log" 2>&1 &
LOG_PID=$!
echo "Log PID: $LOG_PID"
sleep 1
echo ""

# Enviar request
echo -e "${YELLOW}[4/7] Enviando prompt de prueba...${NC}"
TEST_PROMPT='[CONTEXT: This is a Python project using FastAPI/uvicorn. ALL source files use .py extension. There are NO TypeScript, JavaScript, .ts, or .js files anywhere in this codebase.]

Before analyzing any file, verify it exists using Read or Glob. If a file does not exist, say "file not found" — do NOT invent content.

Lee exhaustivamente todos los archivos en vendor/claude-code-proxy/ — el servidor, los transformers, el router, el compressor, streaming, utils, y los tests. Después dame:
1. Un análisis arquitectónico del sistema (cómo fluye un request desde que entra hasta que sale)
2. Identifica los 3 bugs más críticos que encuentres (solo en archivos .py reales que hayas leído)
3. Propón un fix concreto para el bug más grave (con código)'

START=$(date +%s)

# Build payload
PAYLOAD=$(python3 <<PYTHON
import json
payload = {
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 8192,
    "stream": True,
    "messages": [
        {"role": "user", "content": """$TEST_PROMPT"""}
    ]
}
print(json.dumps(payload))
PYTHON
)

# Send request
echo "Enviando a $PROXY_URL/v1/messages..."
curl -s -X POST "$PROXY_URL/v1/messages" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --no-buffer > "$DEST/response.sse" 2>&1 &
CURL_PID=$!

# Wait with progress
TIMEOUT=180
ELAPSED=0
echo -n "Esperando respuesta: "
while [ $ELAPSED -lt $TIMEOUT ]; do
    sleep 5
    ELAPSED=$((ELAPSED + 5))

    LINES=$(wc -l < "$DEST/response.sse" 2>/dev/null || echo "0")
    echo -n "${ELAPSED}s (${LINES} líneas)... "

    if ! kill -0 $CURL_PID 2>/dev/null; then
        echo ""
        echo -e "${GREEN}Request completado${NC}"
        break
    fi
done

END=$(date +%s)
DURATION=$((END - START))
echo ""
echo -e "${GREEN}Duración: ${DURATION}s${NC}"

# Stop log capture
kill $LOG_PID 2>/dev/null || true
wait $LOG_PID 2>/dev/null || true
echo ""

# Capture final metrics
echo -e "${YELLOW}[5/7] Capturando métricas finales...${NC}"
sleep 2
curl -s "$PROXY_URL/api/stats" 2>/dev/null | python3 -m json.tool > "$DEST/final-stats.json" || echo "{}" > "$DEST/final-stats.json"
curl -s "$PROXY_URL/api/logs?n=200" 2>/dev/null | python3 -m json.tool > "$DEST/logs.json" || echo "[]" > "$DEST/logs.json"

# Calculate deltas
FINAL_REQS=$(python3 -c "import json; d=json.load(open('$DEST/final-stats.json')); print(d.get('total_requests',0))" 2>/dev/null || echo "0")
FINAL_COST=$(python3 -c "import json; d=json.load(open('$DEST/final-stats.json')); print(d.get('cost',{}).get('total_usd',0))" 2>/dev/null || echo "0")
TEST_REQS=$((FINAL_REQS - BASELINE_REQS))
TEST_COST=$(python3 -c "print(float('$FINAL_COST') - float('$BASELINE_COST'))" 2>/dev/null || echo "0")

echo "Final: $FINAL_REQS requests, \$$FINAL_COST"
echo "Test: $TEST_REQS requests, \$$TEST_COST"
echo ""

# Analyze results
echo -e "${YELLOW}[6/7] Analizando resultados...${NC}"

python3 <<PYTHON
import json
import re

try:
    stats = json.load(open("$DEST/final-stats.json"))
except:
    stats = {}

try:
    logs = json.load(open("$DEST/logs.json"))
except:
    logs = []

# Filter test logs
test_logs = logs[-$TEST_REQS:] if $TEST_REQS > 0 else logs

# Analysis
intents = {}
models = {}
providers = {}
phases = {}
latency_total = 0
for log in test_logs:
    i = log.get("intent", "?")
    intents[i] = intents.get(i, 0) + 1

    m = log.get("model_used", "?")
    if len(m) > 30:
        m = "..." + m[-27:]
    models[m] = models.get(m, 0) + 1

    p = log.get("provider", "?")
    providers[p] = providers.get(p, 0) + 1

    ph = log.get("phase", "?")
    phases[ph] = phases.get(ph, 0) + 1

    latency_total += log.get("latency_ms", 0)

avg_lat = latency_total // max(len(test_logs), 1) if test_logs else 0

# Output
with open("$DEST/metrics-summary.txt", "w") as f:
    f.write("=" * 50 + "\n")
    f.write(f"FIRE TEST: $LABEL\n")
    f.write("=" * 50 + "\n\n")
    f.write(f"Total requests: {len(test_logs)}\n")
    f.write(f"Total cost: \${float('$TEST_COST'):.6f}\n")
    f.write(f"Duration: ${DURATION}s\n")
    f.write(f"Avg latency: {avg_lat}ms\n\n")

    f.write("INTENT DISTRIBUTION:\n")
    for intent, count in sorted(intents.items(), key=lambda x: -x[1]):
        pct = count * 100 // len(test_logs) if test_logs else 0
        f.write(f"  {intent}: {count} ({pct}%)\n")
    f.write("\n")

    f.write("MODEL DISTRIBUTION:\n")
    for model, count in sorted(models.items(), key=lambda x: -x[1])[:5]:
        pct = count * 100 // len(test_logs) if test_logs else 0
        f.write(f"  {model}: {count} ({pct}%)\n")
    f.write("\n")

    f.write("PHASE DISTRIBUTION:\n")
    for phase, count in sorted(phases.items(), key=lambda x: -x[1]):
        pct = count * 100 // len(test_logs) if test_logs else 0
        f.write(f"  {phase}: {count} ({pct}%)\n")

# Print to console
print("-" * 40)
print("RESUMEN DE MÉTRICAS")
print("-" * 40)
print(f"Requests: {len(test_logs)}")
print(f"Cost: \${float('$TEST_COST'):.6f}")
print(f"Avg Latency: {avg_lat}ms")
print(f"\nIntents: {dict(sorted(intents.items(), key=lambda x: -x[1])[:3])}")
print(f"Phases: {dict(sorted(phases.items()))}")
PYTHON

echo ""

# Extract response text
echo -e "${YELLOW}[7/7] Extrayendo respuesta...${NC}"

python3 <<PYTHON
import json
import re

response_text = []
with open("$DEST/response.sse", "r") as f:
    for line in f:
        if line.startswith("data: ") and line.strip() != "data: [DONE]":
            try:
                data = json.loads(line[6:])
                if data.get("type") == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        response_text.append(delta.get("text", ""))
            except:
                pass

full_response = "".join(response_text)

with open("$DEST/response-text.txt", "w", encoding="utf-8") as f:
    f.write(full_response)

# Quality checks
issues = []
score = 1.0

# CRITICAL: TypeScript hallucination detection in Python project
import re as _re
ts_refs = _re.findall(r'\w+\.(ts|js|tsx|jsx):\d+', full_response)
ts_count = len(ts_refs)
if ts_count > 0:
    issues.append(f"CRITICAL: {ts_count} TypeScript file:line refs in Python project (hallucination)")
    score -= 0.40

# Required: actual Python file:line references
py_refs = _re.findall(r'\w+\.py:\d+', full_response)
py_count = len(py_refs)
if py_count == 0:
    issues.append("No .py:line references found (analysis must cite real Python files)")
    score -= 0.20

# Check for bugs claimed
bug_count = len(_re.findall(r'bug|cr[ií]tico|grave', full_response, _re.IGNORECASE))
if bug_count > 0 and py_count == 0:
    issues.append(f"Claims {bug_count} bugs but no Python file:line references")
    score -= 0.10

# Check for architecture analysis
arch_keywords = ["arquitect", "pipeline", "request flow", "transformer", "routing", "flujo"]
has_arch = any(kw in full_response.lower() for kw in arch_keywords)
if not has_arch:
    issues.append("Missing architectural analysis")
    score -= 0.15

score = max(0.0, min(1.0, score))

with open("$DEST/quality-report.txt", "w", encoding="utf-8") as f:
    f.write("=" * 50 + "\n")
    f.write("QUALITY REPORT\n")
    f.write("=" * 50 + "\n\n")
    f.write(f"Quality Score: {score:.2f}/1.0\n\n")
    f.write(f"Response length: {len(full_response)} chars\n")
    f.write(f"Lines: {len(full_response.split(chr(10)))}\n")
    f.write(f"Bug mentions: {bug_count}\n")
    f.write(f"Python file refs (.py:N): {py_count}\n")
    f.write(f"TypeScript refs (CRITICAL): {ts_count}\n")
    f.write(f"Has architecture: {has_arch}\n\n")

    if issues:
        f.write("Issues:\n")
        for issue in issues:
            f.write(f"  - {issue}\n")

print("-" * 40)
print("QUALITY REPORT")
print("-" * 40)
print(f"Score: {score:.2f}/1.0")
print(f"Response: {len(full_response)} chars")
print(f"Python refs: {py_count}  TypeScript refs: {ts_count}")
print(f"Bugs claimed: {bug_count}")
if issues:
    for issue in issues:
        print(f"  ⚠️  {issue}")
if score >= 0.80:
    print("\n✅ PASS: Quality score >= 80%")
else:
    print(f"\n❌ FAIL: Quality score < 80% ({score:.2f})")
PYTHON

echo ""
echo -e "${GREEN}=== TEST COMPLETO ===${NC}"
echo "Resultados guardados en: ${BLUE}$DEST${NC}"
echo ""
echo "Archivos:"
echo "  📄 metrics-summary.txt    # Métricas agregadas"
echo "  📄 response-text.txt       # Respuesta completa"
echo "  📄 quality-report.txt      # Evaluación de calidad"
echo "  📄 logs.json               # Logs por request"
echo "  📄 docker.log              # Logs del container"
echo ""
echo -e "${YELLOW}Para ver los logs en tiempo real durante la próxima prueba:${NC}"
echo "  docker logs -f $CONTAINER"
