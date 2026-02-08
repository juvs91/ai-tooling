# Guardrails (equipo)

## Reglas de oro
- Nada de pegar texto gigante al chat: siempre `cat archivo | cc-...`
- Local = scan/plan/validación textual (0$)
- Cloud = ejecución con tools SOLO cuando valga la pena

## Token/tiempo (anti-explosión)
- Máximo 1-2 iteraciones por análisis local
- Siempre escribir outputs a `ai-notes/` (no se “queda” en conversación)

## Seguridad
- No subir datos sensibles a free tiers
- Si se usa un modelo externo: solo contenido público/no sensible
- Lo aprendido se aterriza a markdowns internos sin secretos

## Puerta de ejecución
- Prohibido usar agente/tools si NO existe `ai-notes/AI_PLAN.md` marcado como REVISADO
