#!/usr/bin/env bash
# Script para ejecutar serper-mcp-server localmente
# Uso: ./scripts/serper-mcp.sh

SERPER_API_KEY="[REDACTED]"

echo "[serper] Iniciando serper-mcp-server con API key configurada"
echo "[serper] El servidor se ejecutará en http://localhost:3356"
echo "[serper] Presiona Ctrl+C para detener"

# Ejecutar el servidor MCP (se ejecutará indefinidamente)
npx -y serper-search-scrape-mcp-server
