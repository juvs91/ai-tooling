---
name: code-review
description: >
  Performs a complete code review of a Bitbucket PR. Fetches the PR diff, reads relevant local files,
  and generates a structured review document with real findings (bugs, design issues, positives)
  using stack-specific criteria. Supports connect-backend (C# .NET), connect-frontend (React/DevExtreme),
  cpfr-api/pvo-api/ocs (Go), mae-admin (Next.js), mae-e2e (Playwright), y wpc-front
  (Next.js 14 + React 18 + Redux + MSAL). Updates the local JIRA story doc if one exists.
  Optionally posts a summary comment to the Bitbucket PR. All output is in Mexican Spanish (es-MX).
  Trigger: "code review", "review PR", "revisar PR", "hacer code review", "/code-review",
  "review the PR", "code-review PR #NNN", "analizar el PR".
allowed-tools:
  - Read
  - Write
  - Glob
  - Grep
  - Bash
  - mcp__bitbucket__bitbucket_getPullRequest
  - mcp__bitbucket__bitbucket_getPullRequestDiff
  - mcp__bitbucket__bitbucket_getPullRequestChanges
  - mcp__bitbucket__bitbucket_getPR_CommentsAndAction
  - mcp__bitbucket__bitbucket_postPullRequestComment
---

# Skill: code-review

Realiza un code review completo de un PR de Bitbucket. Genera un documento estructurado con
hallazgos reales (bugs, problemas de diseño, positivos) usando criterios específicos por stack.
Todo el output es en español mexicano (es-MX).

---

## CONSTANTES (hardcoded)

```
REVIEWS_DIR  = /Users/arodarte/deacero/bitbucket/wpc-cpfr/workspace/02-code-reviews/
STORIES_DIR  = /Users/arodarte/deacero/bitbucket/wpc-cpfr/workspace/10-management/stories/
REVIEWER     = Alberto Rodarte
```

---

## STEP 1 — Parsear el PR

Aceptar cualquiera de estos formatos de input:

- URL completa: `https://bitbucket.org/deacero/connect-frontend/pull-requests/432`
- Short: `PR #432 connect-frontend`, `432 connect-frontend`, `connect-frontend 432`
- Sin repo (si hay contexto claro del proyecto): `PR #432`

Extraer:
- workspace (siempre `deacero`)
- repo_slug (e.g. `connect-frontend`, `wpc-cpfr`, `connect-backend`)
- pr_id (número entero)

Regex para URL: `bitbucket\.org/([^/]+)/([^/]+)/pull-requests/(\d+)`

Si falta el `repo_slug`, preguntar al usuario antes de continuar.

---

## STEP 2 — Fetch del PR (3 llamadas en paralelo)

Ejecutar en paralelo:

1. **`mcp__bitbucket__bitbucket_getPullRequest`** → metadata
   - Extraer: title, description, author.display_name, source.branch.name,
     destination.branch.name, created_on, state, links.html.href

2. **`mcp__bitbucket__bitbucket_getPullRequestDiff`** → diff completo
   - Extraer: archivos cambiados, líneas añadidas/eliminadas, contenido del diff

3. **`mcp__bitbucket__bitbucket_getPR_CommentsAndAction`** → comentarios existentes
   - Non-fatal: si falla, continuar con `comments = []`

Parámetros para todos: `workspace="deacero"`, `repo_slug=<repo_slug>`, `pull_request_id=<pr_id>`

---

## STEP 3 — Detectar proyecto y stack

Por `repo_slug`:

| repo_slug | Stack | Local path |
|-----------|-------|------------|
| `connect-backend` | C# .NET 6 + Dapper | `/Users/arodarte/deacero/bitbucket/connect-backend` |
| `connect-frontend` | React 16 + JavaScript + DevExtreme + Auth0 | `/Users/arodarte/deacero/bitbucket/connect-frontend` |
| `wpc-cpfr` | Monorepo — ver sub-proyecto por paths | `/Users/arodarte/deacero/bitbucket/wpc-cpfr` |
| `wpc-frontend` | Monorepo — ver sub-proyecto por paths | `/Users/arodarte/deacero/bitbucket/wpc-frontend` |

### Para `wpc-cpfr` — sub-proyecto por prefijo de paths en el diff:

| Prefijo en el diff | Sub-proyecto | Stack | Local path absoluto |
|--------------------|-------------|-------|---------------------|
| `apis/cpfr_api/` | cpfr-api | Go 1.25 + Gin + GORM + PostgreSQL | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/apis/cpfr_api` |
| `apis/pvo_api/` | pvo-api | Go 1.25 + Fiber v2 + PostgreSQL | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/apis/pvo_api` |
| `apis/order_classification_api/` | ocs | Go 1.25 + Fiber v2 + batch workers | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/apis/order_classification_api` |
| `apis/testing/mae-e2e/` | mae-e2e | Playwright + TypeScript | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/apis/testing/mae-e2e` |
| `apps/mae-admin/` | mae-admin | Next.js 16 + React 19 + Bootstrap 5 | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/apps/mae-admin` |
| `analytics/dbt/` | dbt | SQL/Jinja + BigQuery | `/Users/arodarte/deacero/bitbucket/wpc-cpfr/analytics/dbt/inventory-orders` |

Si el diff contiene archivos de múltiples sub-proyectos → informar al usuario y revisar todos.

### Para `wpc-frontend` — sub-proyecto por prefijo de paths en el diff:

| Prefijo en el diff | Sub-proyecto | Stack | Local path absoluto |
|--------------------|-------------|-------|---------------------|
| `frontend/wpc-front/` | wpc-front | Next.js 14.2 + React 18 + Redux Toolkit + MSAL + Material UI 6 | `/Users/arodarte/deacero/bitbucket/wpc-frontend/frontend/wpc-front` |

Branches principales: `development`, `main`. El resto son ramas de trabajo o features.

---

## STEP 4 — Verificar skill custom por proyecto

Buscar con Glob:

```
~/.claude/skills/code-review-{proyecto}/SKILL.md
```

Donde `{proyecto}` puede ser: `connect-backend`, `connect-frontend`, `cpfr-api`, `pvo-api`,
`ocs`, `mae-admin`, `mae-e2e`, `dbt`, `wpc-front`.

- Si existe → Read el archivo y usar sus criterios específicos **además** de los del STEP 5.
- Si no existe → continuar solo con los criterios del STEP 5.

---

## STEP 5 — Leer archivos locales relevantes

Para cada archivo listado en el diff:
- Construir ruta absoluta: `{local_path}/{archivo}`
- Leer con `Read` (archivo completo)
- Omitir: `*.lock`, `*.sum`, `go.sum`, `package-lock.json`, archivos binarios,
  archivos generados (`*.gen.go`, `docs/swagger.json`), assets (`*.png`, `*.svg`, `*.ico`)

Priorizar archivos de lógica: servicios, repositorios, entidades, hooks, controladores, tests.

---

## Criterios de análisis por stack

### Go APIs (cpfr-api, pvo-api, ocs)

| Patrón | Problema |
|--------|---------|
| Nil pointer | Puntero desreferenciado sin nil check previo |
| Error wrapping | Usar `fmt.Errorf("context: %w", err)` — errores no envueltos pierden stack |
| Goroutine leaks | Goroutines sin `context.Done()` ni timeout |
| SQL injection | Queries construidas con concatenación de strings vs placeholders `?` |
| GORM | N+1 queries, falta de `.Error` check después de operaciones |
| Context propagation | Funciones que reciben datos de request pero no `ctx context.Context` |
| Tests | Cobertura de casos edge (nil, empty, error), uso de interfaces para mocks |
| Logging | `logrus.WithFields(...)` vs `fmt.Println` / `log.Printf` |
| Error handling | `_` descartando errores de operaciones que pueden fallar |
| Concurrencia | Race conditions, semaphore usage correcto (ocs: max 10 workers) |
| UTC en timestamps | PVO API debe usar UTC (no `time.Local`) |

### React / connect-frontend (React 16 + JavaScript + DevExtreme)

| Patrón | Problema |
|--------|---------|
| `useEffect` deps | Dependencias faltantes o incorrectas en el array |
| Async sin await | Promises sin await, `.catch()` faltante |
| DevExtreme | `editCellRender` vs `cellRender`, configuración de columns |
| Memory leaks | `isMountedRef` y cleanup en `useEffect` para async operations |
| Auth0 / impersonation | `getSessionItem('ClaUsuario')` puede ser el empleado, no el cliente |
| Silent failures | Bloques `catch` vacíos o que solo hacen `console.error` |
| Fallback incorrecto | Valores de fallback que pueden activar lógica incorrecta |
| PropTypes | Props usadas sin documentar tipo esperado |

### C# .NET / connect-backend (Dapper)

| Patrón | Problema |
|--------|---------|
| `async void` | Siempre `async Task`, nunca `async void` (excepto event handlers) |
| Deadlocks | `.Result` o `.Wait()` en código async bloqueando el thread pool |
| SQL injection en Dapper | Concatenación de strings en queries vs `@param` |
| `IDisposable` | Conexiones/recursos sin `using` o dispose explícito |
| Swallowed exceptions | `catch` vacíos o que solo logean sin re-throw |
| xUnit | Asserts correctos (`Assert.Equal` vs `Assert.True(a == b)`), mocking con Moq |
| Nullable | Null reference sin `?` o guard clause |

### Next.js / mae-admin (Next.js 16 + React 19)

| Patrón | Problema |
|--------|---------|
| Server vs Client | Componentes con `'use client'` que podrían ser Server Components |
| BFF proxy | URLs de backend expuestas en `NEXT_PUBLIC_*` (deben ir server-side) |
| HTTP calls | Uso de `fetch()` directo en hooks/componentes → usar `BaseHttpClient` |
| DataState pattern | Uso de `isLoading`/`error` booleans sueltos → usar `DataState` |
| TypeScript | Tipos `any`, non-null assertions `!` innecesarias, `as unknown as T` |
| React 19 | Mal uso de Server Actions, streaming patterns incorrectos |
| Hydration | Estado inicializado diferente en server vs client |

### Playwright / mae-e2e

| Patrón | Problema |
|--------|---------|
| Selectores frágiles | CSS class selectors vs `data-testid` o ARIA roles |
| Hard waits | `page.waitForTimeout(N)` → usar `page.waitForSelector()` o assertions |
| Test isolation | Tests que dependen del estado de otro test (orden de ejecución) |
| Page Object | Lógica de navegación/interacción en el test en lugar del POM |
| Assertions vacías | Test sin `expect()` que siempre pasa |

### Next.js / wpc-front (Next.js 14.2 + React 18 + Redux Toolkit + MSAL)

| Patrón | Problema |
|--------|---------|
| HTTP calls | Uso de `fetch()` directo en componentes/hooks → siempre usar `apiClient.ts` |
| Redux mutation | Mutación directa del estado en reducers (`state.field = x`) → return nuevo objeto |
| `useEffect` deps | Dependencias faltantes o incorrectas en el array de dependencias |
| MSAL tokens | Tokens Azure en localStorage — no exponer en logs, estado derivado ni `NEXT_PUBLIC_*` |
| Error handling | Bloques `catch` vacíos o que solo hacen `console.error` sin reporte al usuario |
| Async sin await | Promises sin await o `.catch()` faltante en llamadas a API |
| TypeScript strict | Tipos `any`, non-null assertions `!` innecesarias, `as unknown as T` |
| Memory leaks | Timers, suscripciones o requests sin cleanup en `useEffect return` |
| SCSS Modules | Clases hardcoded como strings literales vs `styles.className` |
| RequireAuth | Rutas/páginas protegidas que omiten el guard `<RequireAuth>` |
| Pages vs App Router | Mezcla incorrecta de patrones (Pages Router es el principal; App Router en migración parcial) |
| Redux selector | `useSelector` con selector que retorna objeto nuevo en cada render → usar selectores memoizados |
| ErrorType enum | Errores sin tipar vs uso del enum `ErrorType` definido en `apiClient.ts` |

### DBT (SQL/Jinja + BigQuery)

| Patrón | Problema |
|--------|---------|
| Ref vs hardcoded | Tablas hardcoded en lugar de `{{ ref('model') }}` |
| Tests | Modelos sin tests (unique, not_null en columnas clave) |
| Materialización | `view` vs `table` vs `incremental` correctamente elegida |
| Grain del modelo | Joins que multiplican filas inesperadamente |

---

## STEP 6 — Extraer JIRA key y buscar story doc local

Determinar `STORIES_DIR` según `repo_slug`:
- `wpc-frontend` → `STORIES_DIR = /Users/arodarte/deacero/bitbucket/wpc-cpfr/workspace/10-management/stories/order-entry/`
- cualquier otro → `STORIES_DIR = /Users/arodarte/deacero/bitbucket/wpc-cpfr/workspace/10-management/stories/`

Buscar patrón `[A-Z]+-\d+` en:
- Título del PR
- Descripción del PR (primeros 500 chars)
- Nombre del branch fuente

Si se encuentra clave (e.g. `MAE-1326`):
- Glob: `{STORIES_DIR}/{KEY}-*.md`
- Registrar si existe un story doc local → usar en STEP 9

Si hay múltiples claves → usar la primera encontrada (prioridad: título > branch > descripción)

---

## STEP 7 — Construir filename y detectar modo

**Slug del título del PR:**
1. Transliterar acentos (á→a, é→e, í→i, ó→o, ú→u, ñ→n, ü→u)
2. Lowercase
3. Reemplazar caracteres no alfanuméricos con `-`
4. Colapsar múltiples `-` en uno
5. Truncar a 50 caracteres (cortar en el último `-` antes del límite)
6. No incluir la clave JIRA en el slug (ya va en el filename)

**Filename:**
```
PR-{pr_id}[-{JIRA_KEY}]-{slug}-code-review.md
```

Ejemplos:
- `PR-432-MAE-1326-cpfr-menu-enable-frontend-code-review.md`
- `PR-307-rollback-avg-quantity-code-review.md` ← sin JIRA key

**Detectar modo con Glob:**
- Glob: `{REVIEWS_DIR}/PR-{pr_id}-*.md`
- 0 matches → **Create mode** (archivo nuevo)
- 1 match → **Update mode** (sobreescribir con mismo nombre)
- 2+ matches → Preguntar al usuario cuál actualizar

---

## STEP 8 — Mostrar plan de confirmación

Antes de generar el análisis, mostrar este resumen y pedir confirmación:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 Code Review — Plan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PR:         #{pr_id} — {título}
Repo:       {repo_slug}
Stack:      {stack detectado}
Modo:       [CREATE | UPDATE]
Archivo:    {REVIEWS_DIR}/{filename}

Historia:   {KEY} — {encontrada: sí/no, story doc: sí/no}
            {─ Sin historia JIRA detectada}

Acciones:
  ✓ Leer {N} archivos locales del diff
  ✓ Generar análisis completo con hallazgos reales
  ✓ Escribir doc de code review
  {✓ Actualizar story doc {KEY}-*.md}
  {─ Sin historia JIRA detectada}
  ─ Postear en Bitbucket (se ofrecerá después)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Preguntar: **"¿Procedo con el análisis?"**

Al confirmar → ejecutar el análisis completo leyendo archivos locales y el diff.

---

## STEP 9 — Generar análisis y escribir docs

### 9a — Formato canónico del documento de code review

```markdown
# Code Review — PR #{pr_id}: {PR title}

| Campo | Valor |
|-------|-------|
| PR | #{pr_id} {repo_slug} |
| Historia | {KEY} — {summary} |
| Autor | {author.display_name} |
| Reviewer | Alberto Rodarte |
| Fecha | {YYYY-MM-DD} |
| Branch | {source_branch} → {destination_branch} |
| Estado | [APPROVED / CAMBIOS REQUERIDOS / EN REVISIÓN] |

## Resumen del PR

{Descripción de qué hace el PR, cambios incluidos, contexto}

## Archivos Modificados

| Archivo | Tipo de Cambio |
|---------|---------------|
| path/to/file.go | Nuevo / Modificado / Eliminado — {descripción breve} |

## Hallazgos

### 🔴 Crítico
{Omitir sección si no hay hallazgos críticos}

**C1 — {Título descriptivo del hallazgo}**
Archivo: `path/to/file.go` (línea N)

```{language}
// Código problemático con comentario señalando el problema
```

{Explicación del problema y su impacto}

Corrección sugerida:

```{language}
// Código corregido
```

### 🟡 Mayor
{Omitir sección si no hay hallazgos mayores}

**M1 — {Título}**
Archivo: `path/to/file.go` (línea N)

{Descripción + código + corrección}

### 🟠 Menor
{Omitir sección si no hay hallazgos menores}

**m1 — {Título}**
{Descripción breve, puede no tener código}

## Positivos
✅ {Cosa bien hecha 1}
✅ {Cosa bien hecha 2}

## Resumen de Hallazgos

| # | Severidad | Descripción |
|---|-----------|-------------|
| C1 | 🔴 Crítico | {título} |
| M1 | 🟡 Mayor | {título} |
| m1 | 🟠 Menor | {título} |

## Veredicto

{APPROVED / CAMBIOS REQUERIDOS / EN REVISIÓN}. {Razón principal.
Si hay críticos: listar los items que deben resolverse antes del merge.}
```

**Reglas del análisis:**
- Los hallazgos deben ser **reales**, basados en el diff y los archivos leídos
- Cada hallazgo debe incluir la línea exacta (cuando sea posible)
- Si no hay hallazgos en una categoría → **omitir la sección completa**
- Incluir al menos 2-3 positivos genuinos
- El veredicto debe reflejar los hallazgos:
  - Crítico presente → `CAMBIOS REQUERIDOS`
  - Solo mayor/menor → criterio del reviewer (normalmente `CAMBIOS REQUERIDOS` si Mayor)
  - Solo menor/positivos → `APPROVED`
- JIRA URL base: `https://deacero.atlassian.net/browse/{KEY}`

---

### 9b — Escribir el documento

`Write` el archivo en `{REVIEWS_DIR}/{filename}`

---

### 9c — Actualizar story doc (si existe)

Si se encontró un story doc local en STEP 6:

1. `Read` el archivo de la historia
2. Buscar sección `## Code Reviews`
3. Si existe la sección → agregar fila al final de la tabla existente
4. Si no existe → agregar antes de `## Comentarios` (si existe) o al final del archivo

Formato de la fila:

```markdown
## Code Reviews

| PR | Archivo | Fecha | Veredicto |
|----|---------|-------|-----------|
| [#{pr_id} {repo_slug}]({pr_url}) | [PR-{pr_id}-{KEY}-...](../../02-code-reviews/{filename}) | {YYYY-MM-DD} | {veredicto} |
```

Ruta relativa desde `stories/` a `02-code-reviews/` es `../../02-code-reviews/`

Veredicto: `APPROVED` / `Solicitar cambios` / `En revisión`

`Write` el story doc actualizado.

---

### 9d — Ofrecer post en Bitbucket

Al terminar de escribir, preguntar:

> **"¿Quieres que publique un resumen del review como comentario en el PR de Bitbucket?"**

Si confirma → usar `mcp__bitbucket__bitbucket_postPullRequestComment` con:

```markdown
## Code Review — Alberto Rodarte

| Severidad | Cantidad |
|-----------|---------|
| 🔴 Crítico | N |
| 🟡 Mayor | N |
| 🟠 Menor | N |

### Hallazgos principales
{Lista de 3-5 hallazgos más importantes con descripción de 1 línea cada uno}

**Veredicto: {APPROVED / CAMBIOS REQUERIDOS / EN REVISIÓN}**

{Documento completo: [PR-{pr_id}-{KEY}-...-code-review.md](ruta local)}
```

---

## NOTAS ADICIONALES

- **Idioma**: Todo el output en español mexicano (es-MX). Títulos de secciones, hallazgos,
  veredicto, comentarios de Bitbucket — todo en español.
- **Tone**: Técnico y directo. Los hallazgos deben ser accionables con correcciones concretas.
- **No inventar**: Si el diff es pequeño y no hay problemas, reportar positivos y aprobar.
  No fabricar hallazgos para "parecer útil".
- **Profundidad**: Leer los archivos completos, no solo las líneas del diff. Los bugs suelen
  estar en el contexto alrededor de los cambios.
- **Comentarios existentes**: Si hay comentarios del reviewer original en el PR (STEP 2),
  mencionarlos en el Resumen para evitar duplicar observaciones ya reportadas.
