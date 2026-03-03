# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2026-03-02
- Por: claude-opus-4-6 + jeguzman

---

## Patrones que funcionan

### Arquitectura
- Proxy como abstraccion total: Claude Code no sabe que habla con otro proveedor. Un `.env` cambia todo el backend.
- Hot-reload con bind mount + uvicorn --reload: cambios en `vendor/claude-code-proxy/` aplican sin rebuild

### Herramientas
- Docker bind mount de vendor/ para desarrollo sin rebuild
- `curl http://127.0.0.1:8083/api/stats | jq .` para observabilidad
- `curl "http://127.0.0.1:8083/api/logs?n=20" | jq .` para logs de requests

---

## Anti-patrones / Errores comunes

### Proceso
- MEMORY.md puede sesgar analisis si el agente lo usa como atajo en vez de leer codigo
- Siempre verificar claims contra el codigo actual antes de hacer aserciones

---

## Comandos utiles del proyecto

```bash
# Levantar proxy cloud
cd /Users/jeguzman/ai-tooling && docker compose up proxy_cloud -d

# Ver logs del proxy
docker logs ai-tooling-proxy_cloud-1 --tail 30 -f

# Health check
curl http://127.0.0.1:8083/health | jq .

# Stats del proxy (observabilidad)
curl http://127.0.0.1:8083/api/stats | jq .

# Ultimos 20 request logs
curl "http://127.0.0.1:8083/api/logs?n=20" | jq .

# Test rapido del proxy
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":30,"messages":[{"role":"user","content":"hi"}]}'
```

---

## Notas de sesiones anteriores

> Backups disponibles en:
> - `MEMORY.md.bak-20260302` (memoria completa)
> - `AI_LEARNING.md.bak-20260302` (aprendizajes completos)
> Restaurar despues de la prueba limpia.
