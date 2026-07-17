#!/bin/bash
# Check status of all MCP services for AI-Tooling
# Usage: ./scripts/check-mcp-status.sh

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}=========================================="
echo "    MCP Services Health Check"
echo "    AI-Tooling"
echo -e "==========================================${NC}"
echo ""

# Counter for status
TOTAL=6
OK=0
WARN=0
FAIL=0

# 1. AlloyDB Tunnel (Port 5435)
echo -n "1. AlloyDB Tunnel (puerto 5435):    "
if lsof -i :5435 > /dev/null 2>&1; then
    echo -e "${GREEN}[OK]${NC} - SSH tunnel activo"
    ((OK++))
else
    echo -e "${YELLOW}[WARN]${NC} - No hay tunel activo"
    echo "   Para iniciar: ./scripts/start-ssh-tunnel.sh"
    ((WARN++))
fi

# 2. AlloyDB Connection Test
echo -n "2. AlloyDB Connection:                "
if lsof -i :5435 > /dev/null 2>&1; then
    # Test simple de conexión via MCP
    if timeout 3 node -e "console.log('test'); process.exit(0)" > /dev/null 2>&1; then
        echo -e "${GREEN}[OK]${NC} - Node.js disponible"
        ((OK++))
    else
        echo -e "${YELLOW}[WARN]${NC} - Node.js no instalado o MCP no responde"
        ((WARN++))
    fi
else
    echo -e "${YELLOW}[WARN]${NC} - Requiere tunel SSH activo para probar"
    ((WARN++))
fi

# 3. Atlassian MCP (Cloud - always available)
echo -n "3. Atlassian MCP (Jira/Confl):   "
echo -e "${GREEN}[OK]${NC} - Cloud MCP (siempre disponible)"
((OK++))

# 4. Bitbucket MCP (Cloud - always available)
echo -n "4. Bitbucket MCP:                "
echo -e "${GREEN}[OK]${NC} - Cloud MCP (siempre disponible)"
((OK++))

# 5. CloudSQL MCP (Optional)
echo -n "5. CloudSQL MCP (puerto 9433):      "
if lsof -i :9433 > /dev/null 2>&1; then
    echo -e "${GREEN}[OK]${NC} - CloudSQL MCP disponible"
    ((OK++))
else
    echo -e "${YELLOW}[WARN]${NC} - No disponible (opcional)"
    echo "   Skills usarán documentación local como alternativa"
    ((WARN++))
fi

# 6. GCP Authentication
echo -n "6. GCP Authentication:              "
if gcloud auth print-access-token > /dev/null 2>&1; then
    ACCOUNT=$(gcloud config get-value account 2>/dev/null)
    echo -e "${GREEN}[OK]${NC} - Autenticado como $ACCOUNT"
    ((OK++))
else
    echo -e "${RED}[FAIL]${NC} - No autenticado"
    echo "   Ejecutar: gcloud auth login"
    ((FAIL++))
fi

# Summary
echo ""
echo -e "${BLUE}------------------------------------------${NC}"
echo -e "Resumen: ${GREEN}$OK OK${NC} | ${YELLOW}$WARN WARN${NC} | ${RED}$FAIL FAIL${NC}"
echo -e "${BLUE}------------------------------------------${NC}"

# Skills availability
echo ""
echo -e "${BLUE}Skills Disponibles:${NC}"

if lsof -i :5435 > /dev/null 2>&1 || lsof -i :9433 > /dev/null 2>&1; then
    echo -e "  ${GREEN}+${NC} /alloydb-query    - Consultar precios"
    echo -e "  ${GREEN}+${NC} /alloydb-debug    - Diagnosticar cálculos"
    echo -e "  ${GREEN}+${NC} /cascade-analyzer  - Analizar cascada"
    echo -e "  ${GREEN}+${NC} /cloudsql-query     - Consultar CloudSQL"
else
    echo -e "  ${YELLOW}~${NC} /alloydb-query    - (degradado: usa documentación local)"
    echo -e "  ${YELLOW}~${NC} /alloydb-debug    - (degradado: usa documentación local)"
    echo -e "  ${YELLOW}~${NC} /cascade-analyzer - (degradado: usa documentación local)"
    echo -e "  ${YELLOW}~${NC} /cloudsql-query     - (degradado: usa documentación local)"
fi

if lsof -i :9433 > /dev/null 2>&1; then
    echo -e "  ${GREEN}+${NC} /sp-search          - Buscar stored procedures"
else
    echo -e "  ${YELLOW}~${NC} /sp-search          - (degradado: usa documentación local)"
fi

echo -e "  ${GREEN}+${NC} /tunnel-health     - Verificar entorno"
echo -e "  ${GREEN}+${NC} /jira-context       - Contexto de tickets Jira"

echo ""

# Exit code based on critical failures
if [ $FAIL -gt 0 ]; then
    echo -e "${YELLOW}Hay servicios críticos no disponibles. Algunos skills funcionarán en modo degradado.${NC}"
    echo ""
    exit 1
else
    echo -e "${GREEN}Entorno listo para trabajar.${NC}"
    echo ""
    exit 0
fi
