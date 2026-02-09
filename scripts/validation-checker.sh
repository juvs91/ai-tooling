#!/bin/bash
# Validation Checker - Skill Dinámico para validar reglas de negocio
# Este script carga su documentación desde docs/validation-checker.md

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/_load-skill-doc.sh" "validation-checker"
