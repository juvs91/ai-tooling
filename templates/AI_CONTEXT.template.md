# AI_CONTEXT

## Ticket
- <link o descripción corta>

## Objetivo
- <qué se va a lograr / criterio de éxito>

## Inputs (rutas reales)
- <archivos/dirs a analizar o tocar>

## Scans disponibles (en ai-notes/)
- <ai-notes/*.analysis.md>

## Outputs esperados (en ai-notes/)
- ai-notes/AI_PLAN.md (DRAFT)
- ai-notes/AI_PLAN.md (REVIEWED + Reviewed-by) antes de cloud

## Comandos permitidos (solo estos)
- <lista explícita: cc-scan, cc-plan, cc-agent-cloud, bash -n, cat/sed/grep/ls, etc.>

## Guardrails (core)
- Local: texto (scan/plan/validación), tools OFF.
- Cloud: tools ON solo si AI_PLAN existe y está REVIEWED.
- No inventar paths/comandos.
- Outputs siempre a ai-notes/.

## Definición de “Reviewed”
- STATUS: REVIEWED (o legacy REVIEWED: YES)
- Reviewed-by: <nombre>

## Validación concreta (Definition of Done)
- <comandos exactos para validar>
