# GUARDRAILS (Core)

- Prohibido ejecutar agent/tools si:
  - no existe `ai-notes/AI_PLAN.md`, o
  - `ai-notes/AI_PLAN.md` no contiene exactamente `STATUS: REVIEWED`
- Prohibido inventar paths/comandos.
- Prohibido volcar outputs largos en chat: todo va a `ai-notes/`.
- Local = texto (scan/plan/validación). Cloud = ejecución on-demand.
- Web/MCP scraping (cloud):
  - Serper: max 5 resultados; preferir fetch_url; sin loops; volcar hallazgos a archivo.
  - Puppeteer: solo si fetch_url no alcanza; max 2 screenshots por página.

## Feedback Loop (AI_LEARNING)
- Al finalizar una sesión de trabajo, actualizar `ai-notes/AI_LEARNING.md` con:
  - Decisiones técnicas tomadas y por qué
  - Errores encontrados y cómo se resolvieron
  - Patrones que funcionaron o fallaron
- NO guardar aprendizajes en `.claude/` ni en memoria privada del agente.
- Todo conocimiento del proyecto va a `ai-notes/` (compartido con equipo y futuros agentes).
- El agente DEBE leer `ai-notes/AI_LEARNING.md` al inicio de cada sesión para no repetir errores.
