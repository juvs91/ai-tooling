# Skill: Frontend Analysis

**Cuándo cargar:** Cuando la tarea es analizar, auditar o documentar el frontend de un proyecto Next.js/React.  
**Ref:** ADR-0018-frontend-analysis-skill.md

---

## Regla principal

**Nunca uses `Agent` tool directo para análisis paralelo del frontend.**  
Usa `Workflow` tool con `parallel(agent(), { isolation: 'worktree' })`.

El `Agent` tool no soporta `isolation: 'worktree'`. Sin isolation, agentes concurrentes compiten en el mismo working tree y Kimi K2 genera `tool_use` malformados bajo carga. Tasa de crash documentada: 67%.

---

## Estructura de zonas — divide antes de analizar

Un codebase frontend grande se divide siempre en estas zonas:

| Zona | Paths | Qué buscar |
|------|-------|------------|
| Rutas | `app/` | Páginas, layouts, grupos de rutas, dynamic routes |
| Componentes de dominio | `components/admin/` | Formularios, tablas, flows CRUD por área funcional |
| Componentes UI | `components/ui/` | Primitivos reutilizables (shadcn/Radix) |
| Lógica compartida | `lib/` | API client, auth context, i18n, tipos, utils |
| Hooks | `hooks/` | Custom hooks de dominio |

**Regla de tamaño por agente:** Una zona = un agente. Nunca asignes todo el frontend a un solo agente.

---

## Patrón para análisis read-only (el más común)

```javascript
export const meta = {
  name: 'frontend-analysis',
  description: 'Analiza frontend por zonas y sintetiza en ai-notes/',
  phases: [
    { title: 'Explorar', detail: 'Un agente por zona' },
    { title: 'Sintetizar', detail: 'Combinar hallazgos' },
  ],
}

const ZONES = [
  { name: 'routes',     path: 'app/',              focus: 'Rutas, layouts, páginas' },
  { name: 'domain',     path: 'components/admin/', focus: 'Componentes de negocio' },
  { name: 'ui',         path: 'components/ui/',    focus: 'Componentes reutilizables' },
  { name: 'lib',        path: 'lib/',              focus: 'API client, auth, i18n, tipos' },
  { name: 'hooks',      path: 'hooks/',            focus: 'Custom hooks' },
]

phase('Explorar')
const findings = await parallel(ZONES.map(z => () =>
  agent(
    `Explora ${z.path}. Foco: ${z.focus}.
     Lista archivos, props principales, patrones de uso.
     NO edites archivos. Devuelve hallazgos estructurados.`,
    { label: `explore:${z.name}`, phase: 'Explorar', isolation: 'worktree' }
  )
))

phase('Sintetizar')
const synthesis = await agent(
  `Sintetiza estos hallazgos por zona y escríbelos en ai-notes/frontend/.
   ${JSON.stringify(findings.filter(Boolean))}`,
  { label: 'synthesize', phase: 'Sintetizar' }
)

return synthesis
```

**Por qué `isolation: 'worktree'` en agentes de solo lectura:**  
Los agentes de exploración no escriben código, pero el semáforo `MAX_CONCURRENT_PER_PROVIDER=2` limita cuántos se ejecutan simultáneamente. Worktree isolation garantiza que si algún agente inesperadamente intenta escribir, lo hace en una copia aislada — no en el proyecto real.

---

## Patrón para documentar gaps (frontend vs ai-notes)

```javascript
// Fase 1: Explorar frontend Y documentación en paralelo
const [frontendMap, docsMap] = await parallel([
  () => agent('Lista todos los componentes y hooks en components/, lib/, hooks/. Sin editar.', 
              { isolation: 'worktree', label: 'frontend-map' }),
  () => agent('Lista todo lo documentado en ai-notes/frontend/. Qué cubre cada doc.', 
              { isolation: 'worktree', label: 'docs-map' }),
])

// Fase 2: Identificar gaps
const gaps = await agent(
  `Compara este mapa del frontend:\n${frontendMap}\n\n` +
  `Con esta documentación:\n${docsMap}\n\n` +
  `Identifica qué está sin documentar y clasifica por severidad (alto/medio/bajo).`,
  { label: 'gap-analysis' }
)
```

---

## Anti-patrones — nunca hagas esto

```javascript
// ❌ Agent tool directo para múltiples análisis
agent("Analiza app/ y components/ y lib/ y hooks/ exhaustivamente")

// ❌ Sin isolation en Workflow paralelo
await parallel([
  () => agent('Analiza app/'),
  () => agent('Analiza components/'),
])

// ❌ Un solo agente para todo el frontend
agent("Dame un análisis completo de todo el código del frontend de este proyecto")
```

---

## Síntesis de resultados

Siempre escribe los hallazgos en `ai-notes/frontend/`, no los acumules en el contexto de la conversación:

```
ai-notes/frontend/
  admin-track.md       ← mapa de rutas admin
  api-client.md        ← funciones de lib/api.ts
  auth-flow.md         ← flujo de autenticación
  i18n-guide.md        ← guía de internacionalización
  components-reference.md ← índice de componentes UI
```

Si el archivo ya existe, actualízalo en lugar de recrearlo.

---

## Límites por sesión

- Máximo 3 zonas por ejecución de Workflow (el semáforo limita a 2 concurrentes)
- Si el análisis requiere más de 3 zonas, corre dos Workflows secuenciales
- Nunca lances más de 5 agentes de análisis en una sola sesión sin sintetizar primero
