# Serper MCP - Documentación

## Descripción
Scripts para gestionar el servidor MCP local de Serper (`serper-mcp-server`).

## Archivos

### 1. **serper-mcp.py** (Python)
Script multi-plataforma para iniciar/detener/verificar el estado del servidor Serper.

**Dependencias:**
- Python 3.9+ (incluido en `requirements.txt`)
- `signal` (módulo estándar de Python)
- `subprocess` (módulo estándar de Python)

**Uso:**
```bash
# Iniciar servidor (bloqueante)
./scripts/serper-mcp.py start

# Verificar estado
./scripts/serper-mcp.py status

# Detener servidor
./scripts/serper-mcp.py stop
# O con Ctrl+C
```

**Características:**
- Detección de sistema operativo (macOS, Linux, Windows)
- Manejo de señales SIGINT/SIGTERM para detener gracefully
- Comprobación de disponibilidad del comando `npx`
- Timeout al iniciar servidor (2s)
- Timeout al detener servidor (5s)
- Indicador de estado (corriendo, detenido, error)

### 2. **serper-mcp.sh** (Bash - Legacy)
Script simplificado que puede usarse como fallback o si Python no está disponible.

**Uso:**
```bash
# Ejecutar con bash (si Python está disponible)
bash scripts/serper-mcp.sh start

# Ejecutar con Python (método recomendado)
python3 scripts/serper-mcp.py start
```

## Variables de Entorno

Puedes configurar estas variables en `.mcp.json` para personalizar el comportamiento:

| Variable | Descripción | Ejemplo |
|---------|-----------|---------|
| `SERPER_API_KEY` | API key de Serper | `"180da83ea3909b95c..."` |
| `SERPER_PORT` | Puerto del servidor | `3356` |
| `SERPER_HOST` | Host del servidor | `localhost` |

## Flujo de Trabajo

1. **Inicio**: El script Python detecta si `npx` está disponible
2. **Ejecución**: Inicia `npx -y serper-search-scrape-mcp-server` con el API key
3. **Monitoreo**: Captura el proceso hijo para permitir `stop` posterior
4. **Detención**: Maneja SIGINT/SIGTERM para detener el servidor gracefully

## Notas de Instalación

El servidor Serper se ejecuta como paquete NPM, por lo que requiere:
- Node.js instalado en el sistema
- `npx` disponible en el PATH

## Actualización de .mcp.json.template

El archivo `.mcp.json.template` se puede actualizar para incluir este script como ejemplo:

```json
{
  "_comment": "Ejemplo de configuración para serper-mcp-server",
  "serper": {
    "command": "python3",
    "args": ["scripts/serper-mcp.py"],
    "env": {
      "SERPER_API_KEY": "your-api-key-here"
    }
  }
}
```
