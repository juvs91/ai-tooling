# AI_CONTEXT - Ejemplo: OAuth2 Google Login

## Ticket
- JIRA-1234: Implementar login con Google OAuth2

## Objetivo
- Permitir que usuarios se autentiquen con su cuenta de Google
- Criterio de éxito: Usuario puede hacer login, recibir token JWT, ver su perfil

## Inputs (rutas reales)
- src/auth/ (directorio actual de autenticación)
- src/routes/auth.ts (rutas existentes)
- package.json (dependencias actuales)
- .env.example (variables de entorno template)

## Scans disponibles (en ai-notes/)
- ai-notes/auth.analysis.md (generado por cc-scan src/auth/)
- ai-notes/routes.analysis.md (generado por cc-scan src/routes/)

## Outputs esperados (en ai-notes/)
- ai-notes/AI_PLAN.md (DRAFT) → después de cc-plan
- ai-notes/AI_PLAN.md (REVIEWED + Reviewed-by: jeguzman) → antes de cc-agent-cloud

## Comandos permitidos (solo estos)
- cc-scan src/auth/ src/routes/
- cc-plan
- cc-agent-cloud (solo después de REVIEWED)
- npm run tsc -- --noEmit (validación sintaxis)
- npm test -- --testPathPattern=auth (tests específicos)
- cat, grep, ls (lectura)

## Guardrails (core)
- Local: texto (scan/plan/validación), tools OFF
- Cloud: tools ON solo si AI_PLAN existe y está REVIEWED
- No inventar paths/comandos
- Outputs siempre a ai-notes/
- No modificar archivos fuera de src/auth/ sin aprobación

## Definición de "Reviewed"
- STATUS: REVIEWED
- Reviewed-by: jeguzman

## Validación concreta (Definition of Done)
```bash
# 1. Sintaxis válida
npm run tsc -- --noEmit

# 2. Tests pasan
npm test -- --testPathPattern=auth

# 3. Endpoint responde
curl -I http://localhost:3000/auth/google

# 4. Variables de entorno documentadas
grep -E "GOOGLE_CLIENT" .env.example
```

## Dependencias nuevas requeridas
- googleapis o passport-google-oauth20
- jsonwebtoken (si no existe)

## Riesgos identificados
- Secrets de Google en .env (no commitear)
- Rate limiting de Google API
- Manejo de refresh tokens
