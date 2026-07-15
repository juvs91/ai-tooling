# ADR-0018: Frontend Analysis Skill para Kimi K2

**Estado:** Accepted  
**Fecha:** 2026-07-14

## Contexto

Kimi K2 crasheó al 67% al intentar analizar el frontend de school-system usando `Agent` tool directo con 3 agentes paralelos sin worktree isolation. El modelo no tiene una plantilla de cómo estructurar análisis de codebases grandes — improvisa y abarca demasiado de una sola vez.

## Decisión

Crear `.agents/skills/frontend/frontend-analysis/SKILL.md` — un skill que guía a Kimi K2 a:
1. Usar `Workflow` tool (no `Agent`) para análisis paralelo
2. Dividir el codebase en zonas manejables (app/, components/, lib/, hooks/)
3. Usar `isolation: 'worktree'` para agentes de lectura paralela
4. Sintetizar en fases, no en un solo paso exhaustivo
5. Escribir hallazgos en `ai-notes/frontend/` en lugar de acumularlos en contexto

## Alternativas rechazadas

- Confiar en el prompt del usuario: ya demostró ser insuficiente ("analiza exhaustivamente TODO el front")
- Parchear el proxy para limitar agentes: ataca el síntoma, no el comportamiento

## Consecuencias

- Kimi K2 tiene una referencia de patrón correcta cuando detecta que la tarea es análisis de frontend
- El hook `worktree-isolation-gate.sh` actúa como red de seguridad si el modelo se desvía
