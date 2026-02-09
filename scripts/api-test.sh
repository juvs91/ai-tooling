#!/bin/bash
# API Test - Skill Dinámico para probar integración
# Este script carga su documentación desde docs/api-test.md

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/_load-skill-doc.sh" "api-test"
