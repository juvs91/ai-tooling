# MCP Servers - Configuración Dual

## Descripción

Este proyecto soporta **dos modos de ejecución** para el servidor MCP Serper:

1. **serper-mcp.sh** (Bash) - Script simple para iniciar/parar
2. **serper-mcp.py** (Python) - Script robusto multi-plataforma

## Configuración

Para elegir el modo de ejecución, edita `.mcp.json`:

```json
{
  "mcpServers": {
    "alloydb": {...},
    "atlassian": {...},
    ...

    // MODO 1: Serper como principal (búsquedas)
    "serper": {
      "command": "bash",
      "args": ["scripts/serper-mcp.sh"],
      "env": {
        "SERPER_API_KEY": "..."
      }
    },

    // MODO 2: Playwright como principal (pruebas/interacción web)
    "playwright": {
      "command": "bash",
      "args": ["scripts/serper-mcp.sh"],
      "env": {
        "PLAYWRIGHT_BROWSERS_PATH": "..."
      }
    },

    // MODO 3: Solo serper (complemento, no principal)
    "serper": {
      "command": "python3",
      "args": ["scripts/serper-mcp.py"],
      "env": {
        "SERPER_API_KEY": "..."
      }
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@executeautomation/playwright-mcp-server"],
      "env": {
        "PLAYWRIGHT_BROWSERS_PATH": "..."
      }
    }
}
```

## Ejemplos de Uso

### Opción 1: Serper como único servidor (búsquedas)
```json
{
  "mcpServers": {
    "alloydb": {...},
    "atlassian": {...},
    "squit": {...},
    "serper": {
      "command": "bash",
      "args": ["scripts/serper-mcp.sh"],
      "env": {
        "SERPER_API_KEY": "..."
      }
    }
  }
}
```

### Opción 2: Playwright como único servidor (pruebas)
```json
{
  "mcpServers": {
    "alloydb": {...},
    "atlassian": {...},
    "squit": {...},
    "playwright": {
      "command": "npx",
      "args": ["-y", "@executeautomation/playwright-mcp-server"],
      "env": {
        "PLAYWRIGHT_BROWSERS_PATH": "..."
      }
    }
  }
}
```

### Opción 3: Ambos complementarios (pruebas + búsquedas)
```json
{
  "mcpServers": {
    "alloydb": {...},
    "atlassian": {...},
    "squit": {...},
    "serper": {
      "command": "bash",
      "args": ["scripts/serper-mcp.sh"],
      "env": {
        "SERPER_API_KEY": "..."
      }
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@executeautomation/playwright-mcp-server"],
      "env": {
        "PLAYWRIGHT_BROWSERS_PATH": "..."
      }
    }
  }
}
```

## Scripts

### 1. serper-mcp.sh (Bash)
Uso: `./scripts/serper-mcp.sh start`
- Detecta si `npx` está disponible
- Inicia el servidor Serper en puerto 3356
- Bloqueante (espera a Ctrl+C para detener)

### 2. serper-mcp.py (Python)
Uso: `./scripts/serper-mcp.py start/stop/status`
- Detección de sistema operativo (macOS, Linux, Windows)
- Manejo de señales SIGINT/SIGTERM
- Timeout configurable al iniciar/detener
- Multi-plataforma (funciona en todos)
- Estado del servidor (corriendo/detenido/error)

## Documentación Adicional

Ver `scripts/serper-mcp.md` para guías completas de uso.
