#!/bin/bash
# fire-test-cc.sh - Prueba de fuego usando Claude Code CLI (modo --print)
# Usage: ./scripts/fire-test-cc.sh [label]

set -e

LABEL="${1:-fire-test-cc-$(date +%Y%m%d-%H%M%S)}"
DEST="ai-notes/${LABEL}"
PROXY_URL="http://localhost:8085"
SETTINGS_FILE="$HOME/.claude/settings.test.json"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Crear settings.test.json si no existe
if [ ! -f "$SETTINGS_FILE" ]; then
  echo -e "${YELLOW}Creando $SETTINGS_FILE...${NC}"
  cat > "$SETTINGS_FILE" <<'EOF'
{
  "alwaysThinkingEnabled": false,
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8085",
    "ANTHROPIC_AUTH_TOKEN": "dummy",
    "ANTHROPIC_API_KEY": ""
  }
}
EOF
fi

mkdir -p "$DEST"
echo -e "${GREEN}=== FIRE TEST CC: $LABEL ===${NC}"
echo "Destino: $DEST"
echo "Proxy: $PROXY_URL"
echo ""

# Health check
echo -e "${YELLOW}[1/6] Health check...${NC}"
HEALTH=$(curl -s "$PROXY_URL/health" | jq -r '.status')
if [ "$HEALTH" != "healthy" ]; then
  echo -e "${RED}ERROR: Proxy no saludable (status: $HEALTH)${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Proxy healthy${NC}"
echo ""

# Baseline metrics
echo -e "${YELLOW}[2/6] Capturando baseline...${NC}"
curl -s "$PROXY_URL/api/stats" > "$DEST/baseline.json"
BASELINE_REQS=$(jq -r '.total_requests // 0' "$DEST/baseline.json")
BASELINE_COST=$(jq -r '.cost.total_usd // 0' "$DEST/baseline.json")
echo "Baseline: $BASELINE_REQS requests, \$$BASELINE_COST"
echo ""

# Prompt de prueba
PROMPT='[CONTEXT: This is a Python project using FastAPI/uvicorn. ALL source files use .py extension. There are NO TypeScript, JavaScript, .ts, or .js files anywhere in this codebase.]

Before analyzing any file, verify it exists using Read or Glob. If a file does not exist, say "file not found" — do NOT invent content.

Lee exhaustivamente todos los archivos en vendor/claude-code-proxy/ — el servidor, los transformers, el router, el compressor, streaming, utils, y los tests. Después dame:
1. Un análisis arquitectónico del sistema (cómo fluye un request desde que entra hasta que sale)
2. Identifica los 3 bugs más críticos que encuentres (solo en archivos .py reales que hayas leído)
3. Propón un fix concreto para el bug más grave (con código)'

# Ejecutar Claude Code CLI en modo --print (non-interactive)
echo -e "${YELLOW}[3/6] Ejecutando Claude Code CLI (--print mode)...${NC}"
echo "Prompt: $(echo "$PROMPT" | head -1)..."
echo ""

START=$(date +%s)

# Ejecutar y capturar salida
echo "$PROMPT" | claude -p \
  --settings "$SETTINGS_FILE" \
  --output-format text \
  --max-budget-usd 1.00 \
  > "$DEST/cc-output.txt" 2>&1 &

CC_PID=$!

# Monitorear progreso
TIMEOUT=600  # 10 minutos para análisis completo
ELAPSED=0
echo -n "Progreso: "
while [ $ELAPSED -lt $TIMEOUT ]; do
  sleep 10
  ELAPSED=$((ELAPSED + 10))

  # Ver tamaño de output
  if [ -f "$DEST/cc-output.txt" ]; then
    SIZE=$(wc -c < "$DEST/cc-output.txt" 2>/dev/null || echo "0")
    echo -n "${ELAPSED}s (${SIZE} bytes)... "
  else
    echo -n "${ELAPSED}s... "
  fi

  if ! kill -0 $CC_PID 2>/dev/null; then
    echo ""
    echo -e "${GREEN}✓ Claude CLI completado${NC}"
    break
  fi
done

# Matar si timeout
if kill -0 $CC_PID 2>/dev/null; then
  echo ""
  echo -e "${YELLOW}⚠ Timeout, matando proceso...${NC}"
  kill $CC_PID 2>/dev/null || true
  wait $CC_PID 2>/dev/null || true
fi

END=$(date +%s)
DURATION=$((END - START))
echo -e "${GREEN}Duración: ${DURATION}s${NC}"
echo ""

# Capturar métricas finales
echo -e "${YELLOW}[4/6] Capturando métricas finales...${NC}"
sleep 1
curl -s "$PROXY_URL/api/stats" > "$DEST/final-stats.json"
curl -s "$PROXY_URL/api/logs?n=500" > "$DEST/logs.json"
docker logs ai-tooling-proxy_test-1 --tail 200 > "$DEST/docker.log" 2>&1
echo "✓ Métricas capturadas"
echo ""

# Analizar resultados
echo -e "${YELLOW}[5/6] Analizando resultados...${NC}"

python3 <<PYTHON
import json

try:
    with open("$DEST/baseline.json") as f:
        baseline = json.load(f)
except:
    baseline = {}

try:
    with open("$DEST/final-stats.json") as f:
        final = json.load(f)
except:
    final = {}

test_reqs = final.get('total_requests', 0) - baseline.get('total_requests', 0)
test_cost = final.get('cost', {}).get('total_usd', 0) - baseline.get('cost', {}).get('total_usd', 0)

with open("$DEST/summary.txt", "w") as f:
    f.write("=" * 50 + "\n")
    f.write(f"FIRE TEST CC: $LABEL\n")
    f.write("=" * 50 + "\n\n")
    f.write("Duration: $DURATION s\n")
    f.write(f"Requests: {test_reqs}\n")
    f.write(f"Cost: \${test_cost:.6f}\n")

print("-" * 40)
print("RESUMEN")
print("-" * 40)
print(f"Requests: {test_reqs}")
print(f"Cost: \${test_cost:.6f}")
print("Duration: $DURATION s")
PYTHON

echo ""

# Calcular quality score
echo -e "${YELLOW}[6/6] Generando quality report...${NC}"

python3 <<PYTHON
import re

try:
    with open("$DEST/cc-output.txt", "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
except:
    content = ""

# ── Autonomous agent tool usage — parse docker.log for actual CC tool invocations ──
# stream_quality logs: "[stream-refinement] SKIP: tool_use_count=N" per turn
total_tool_calls = 0
try:
    with open("$DEST/docker.log", "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.search(r'tool_use_count=(\d+)', line)
            if m:
                total_tool_calls += int(m.group(1))
except:
    pass

# Quality checks
issues = []
score = 1.0

response_len = len(content)
lines = len(content.split('\n'))

# AUTONOMOUS AGENT: Tool usage (CRITICAL — agent must use tools, not answer from memory)
# Each "turn" with tools is logged by stream_quality. 0 total = answered purely from context.
if total_tool_calls == 0:
    issues.append("CRITICAL: 0 tool invocations — agent answered from memory/context, did not explore codebase")
    score -= 0.40
elif total_tool_calls < 5:
    issues.append(f"LOW TOOL USAGE: only {total_tool_calls} tool calls — minimal codebase exploration")
    score -= 0.15

# CRITICAL: TypeScript hallucination detection in Python project
ts_refs = re.findall(r'\w+\.(ts|js|tsx|jsx):\d+', content)
ts_count = len(ts_refs)
if ts_count > 0:
    issues.append(f"CRITICAL: {ts_count} TypeScript file:line refs in Python project (hallucination)")
    score -= 0.40

# Required: actual Python file:line references
py_refs = re.findall(r'\w+\.py:\d+', content)
py_count = len(py_refs)
if py_count == 0:
    issues.append("No .py:line references found (analysis must cite real Python files)")
    score -= 0.20

# Architecture check
arch_keywords = ["arquitect", "pipeline", "request flow", "transformer", "routing", "flujo", "servidor"]
has_arch = any(kw in content.lower() for kw in arch_keywords)
if not has_arch:
    issues.append("Missing architectural analysis")
    score -= 0.15

# Fix proposal check — keyword + code block (not restricted to def/class, fixes modify existing code)
# NOTE: backticks escaped as \` to avoid bash heredoc command-substitution interpretation
has_code_blocks = bool(re.search(r'\`\`\`', content))
has_fix = bool(re.search(r'(fix|soluci[oó]n|propuesta|corregi|arregl)', content, re.IGNORECASE)) and has_code_blocks
bug_count = len(re.findall(r'bug|cr[ií]tico|grave', content, re.IGNORECASE))
if not has_fix and response_len > 500:
    issues.append("Missing concrete fix proposal")
    score -= 0.15
if not has_code_blocks and response_len > 500:
    issues.append("Missing code blocks")
    score -= 0.10

score = max(0.0, min(1.0, score))

with open("$DEST/quality-report.txt", "w") as f:
    f.write("=" * 50 + "\n")
    f.write("QUALITY REPORT\n")
    f.write("=" * 50 + "\n\n")
    f.write(f"Quality Score: {score:.2f}/1.0\n")
    f.write(f"Tool invocations (CC): {total_tool_calls}\n")
    f.write(f"Response length: {response_len} chars\n")
    f.write(f"Lines: {lines}\n")
    f.write(f"Bugs claimed: {bug_count}\n")
    f.write(f"Python file refs (.py:N): {py_count}\n")
    f.write(f"TypeScript refs (CRITICAL): {ts_count}\n")
    f.write(f"Has architecture: {has_arch}\n")
    f.write(f"Has fix proposal: {has_fix}\n")
    f.write(f"Has code blocks: {has_code_blocks}\n")
    if issues:
        f.write("\nIssues:\n")
        for i in issues:
            f.write(f"  - {i}\n")

print("-" * 40)
print("QUALITY REPORT")
print("-" * 40)
print(f"Score: {score:.2f}/1.0")
print(f"Tool invocations (CC): {total_tool_calls}")
print(f"Response: {response_len} chars ({lines} lines)")
print(f"Python refs: {py_count}  TypeScript refs: {ts_count}")
print(f"Bugs claimed: {bug_count}")
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
echo "  📄 cc-output.txt           # Respuesta completa del Claude CLI"
echo "  📄 summary.txt              # Resumen de métricas"
echo "  📄 quality-report.txt       # Evaluación de calidad"
echo "  📄 logs.json                # Logs por request"
echo "  📄 docker.log               # Logs del container"
echo ""
echo -e "${YELLOW}Para ver la respuesta:${NC}"
echo "  cat $DEST/cc-output.txt"
echo ""
echo -e "${YELLOW}Para ver los logs en tiempo real:${NC}"
echo "  docker logs -f ai-tooling-proxy_test-1"
