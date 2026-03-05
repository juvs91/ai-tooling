#!/bin/bash
# test-intent-enforcement.sh - Prueba cada intent del IntentEnforcementTransformer

set -e

PROXY="http://127.0.0.1:8085"
DEST="ai-notes/intent-validation-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Intent Enforcement Transformer Validation ===${NC}"
echo "Destino: $DEST"
echo ""

# Test 1: READ intent - debe inyectar "tool calls immediately"
echo -e "${YELLOW}[1/5] Testing READ intent...${NC}"
curl -s -X POST $PROXY/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":100,"messages":[{"role":"user","content":"Lee el archivo server.py y dime qué hace"}]}' \
  > "$DEST/read_response.json"
echo "✓ READ response saved"
echo ""

# Test 2: PLAN intent - debe inyectar "STRUCTURED output"
echo -e "${YELLOW}[2/5] Testing PLAN intent...${NC}"
curl -s -X POST $PROXY/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":100,"messages":[{"role":"user","content":"Haz un plan para refactorizar el código del proxy"}]}' \
  > "$DEST/plan_response.json"
echo "✓ PLAN response saved"
echo ""

# Test 3: BUILDING intent - debe inyectar "Edit, Write tools"
echo -e "${YELLOW}[3/5] Testing BUILDING intent...${NC}"
curl -s -X POST $PROXY/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":100,"messages":[{"role":"user","content":"Modifica el archivo server.py para agregar un log"}]}' \
  > "$DEST/building_response.json"
echo "✓ BUILDING response saved"
echo ""

# Test 4: SYNTHESIZING intent - debe inyectar "DO NOT make new tool calls"
echo -e "${YELLOW}[4/5] Testing SYNTHESIZING intent...${NC}"
# SYNTHESIZING requiere historial, así que simulamos con un prompt más largo
curl -s -X POST $PROXY/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":100,"messages":[{"role":"user","content":"Después de leer todos los archivos, genera un resumen ejecutivo"}]}' \
  > "$DEST/synthesizing_response.json"
echo "✓ SYNTHESIZING response saved"
echo ""

# Test 5: CHAT intent - NO debe inyectar nada
echo -e "${YELLOW}[5/5] Testing CHAT intent...${NC}"
curl -s -X POST $PROXY/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":100,"messages":[{"role":"user","content":"Hola, ¿cómo estás?"}]}' \
  > "$DEST/chat_response.json"
echo "✓ CHAT response saved"
echo ""

# Capturar logs del proxy
echo -e "${YELLOW}[Capturing proxy logs...]${NC}"
docker logs ai-tooling-proxy_test-1 --tail 100 2>&1 | grep -E "INTENT-ENFORCEMENT|intent_enforcement|system=" > "$DEST/proxy_logs.txt" 2>&1 || true
echo "✓ Logs saved"

# Analizar enforcement
echo -e "${YELLOW}[Analyzing enforcement...]${NC}"
python3 <<PYTHON
import json

# Verificar si INTENT-ENFORCEMENT aparece en logs
with open("$DEST/proxy_logs.txt", "r") as f:
    logs = f.read()

print("=" * 50)
print("INTENT ENFORCEMENT VALIDATION")
print("=" * 50)

if "INTENT-ENFORCEMENT" in logs:
    print("✅ Intent enforcement prompts están siendo inyectados")
    lines = logs.split("\n")
    for line in lines:
        if "INTENT-ENFORCEMENT" in line or "intent_enforcement" in line:
            print(f"  {line}")
else:
    print("⚠️  No se encontraron logs de INTENT-ENFORCEMENT")
    print("  Nota: Los prompts se inyectan en request.system, no siempre aparecen en logs")

print()
print("Verificación de respuestas:")

# Verificar READ
with open("$DEST/read_response.json") as f:
    read_resp = json.load(f)
    read_text = read_resp.get("content", [{}])[0].get("text", "")
    if "Glob" in read_text or "Read" in read_text or "Grep" in read_text:
        print("✅ READ: Empezó con tool calls")
    else:
        print("⚠️  READ: No empezó con tool calls visibles")

# Verificar PLAN
with open("$DEST/plan_response.json") as f:
    plan_resp = json.load(f)
    plan_text = plan_resp.get("content", [{}])[0].get("text", "")
    if "##" in plan_text or "1." in plan_text or "plan" in plan_text.lower():
        print("✅ PLAN: Tiene estructura")
    else:
        print("⚠️  PLAN: No tiene estructura clara")

# Verificar BUILDING
with open("$DEST/building_response.json") as f:
    build_resp = json.load(f)
    build_text = build_resp.get("content", [{}])[0].get("text", "")
    if "Edit" in build_text or "Write" in build_text or "modificar" in build_text.lower():
        print("✅ BUILDING: Menciona herramientas de edición")
    else:
        print("⚠️  BUILDING: No menciona herramientas de edición")

# Verificar SYNTHESIZING
with open("$DEST/synthesizing_response.json") as f:
    synth_resp = json.load(f)
    synth_text = synth_resp.get("content", [{}])[0].get("text", "")
    if "resumen" in synth_text.lower() or "summary" in synth_text.lower():
        print("✅ SYNTHESIZING: Genera resumen")
    else:
        print("⚠️  SYNTHESIZING: No genera resumen claro")

# Verificar CHAT (no debería tener enforcement)
with open("$DEST/chat_response.json") as f:
    chat_resp = json.load(f)
    chat_text = chat_resp.get("content", [{}])[0].get("text", "")
    if "Hola" in chat_text or "¿cómo" in chat_text:
        print("✅ CHAT: Respuesta conversacional normal")
    else:
        print("⚠️  CHAT: Respuesta inesperada")

PYTHON

echo ""
echo -e "${GREEN}=== VALIDATION COMPLETE ===${NC}"
echo "Resultados guardados en: ${BLUE}$DEST${NC}"
echo ""
echo "Archivos:"
echo "  📄 read_response.json"
echo "  📄 plan_response.json"
echo "  📄 building_response.json"
echo "  📄 synthesizing_response.json"
echo "  📄 chat_response.json"
echo "  📄 proxy_logs.txt"
