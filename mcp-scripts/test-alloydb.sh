#!/usr/bin/env bash
# Prueba rápida de AlloyDB MCP
echo "=== Prueba AlloyDB MCP ==="

source .env 2>/dev/null || {
    echo "ERROR: No se pudo cargar .env"
    exit 1
}

echo "Verificando configuración..."
echo "  URL: ${DB_MAIN_URL}"
echo "  User: ${DB_MAIN_USER}"
echo "  DB Name: ${DB_MAIN_NAME}"

echo ""
echo "Iniciando AlloyDB MCP (10s timeout)..."
timeout 10s node /Users/jeguzman/.nvm/versions/node/v20.20.0/lib/node_modules/postgres-mcp/dist/index.js 2>&1 || echo "❌ AlloyDB no responde" || echo "✅ AlloyDB funcionando"

echo ""
echo "Para usar AlloyDB en Claude Code:"
echo "  1. Asegúrate que AlloyDB esté corriendo"
echo "  2. El MCP debería aparecer en .mcp.json con configuración completa"
echo "  3. Los datos están en: ${DB_MAIN_URL}"
