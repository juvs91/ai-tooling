# Verificación en vivo (fire test) de ADR-0037/0036/0038/0039/0040 — 2026-08-03

**Contexto**: tras shippear el framework de state-assertion (ADR-0036/0038/0039/0040)
y el fix del bug de ordering de ADR-0037 (`FragileModelPlanToolsTransformer`), se
corrieron 2 pruebas de fuego reales contra el proxy desplegado (`ai-tooling-proxy_cloud-1`),
usando Kimi K2 vía Claude Code en el proyecto `school-system`. Objetivo: confirmar
en tráfico real, no solo en tests unitarios, que las mitigaciones funcionan.

## Prueba 1 — Investigar auth + escribir plan

**Prompt**: investigar autenticación del proyecto y escribir un plan (plan mode).

**Hallazgos**:
- `EnterPlanMode` fue bloqueado inicialmente por el hook local `skill-load-gate.sh`
  (no proxy-side) — Kimi leyó el mensaje de error, siguió la remediación exacta
  indicada (leer AGENTS.md → leer SKILL.md), y reintentó con éxito. Comportamiento
  de recuperación correcto, muy distinto al incidente original.
- Confusión real de nombre de tool: Kimi intentó llamar `ExitPlanMode` pero invocó
  `ExitWorktree` por error (nombres similares, prefijo `Exit*`). Se auto-corrigió
  ("Ups, llamé ExitWorktree por error") y llamó `ExitPlanMode` correctamente acto
  seguido — por eso el plan sí se abrió en la UI de Claude Code pese a que el grep
  inicial del transcript no encontraba la tool call (el .txt exportado no capturaba
  el CoT donde ocurrió la auto-corrección).
- CoT revelador: Kimi explícitamente recordó la regla de ToolSearch para deferred
  tools ("según el system-reminder, debo usar ToolSearch primero") pero la descartó
  por inducción defectuosa ("ya lo intenté antes y funcionó sin ToolSearch"). Patrón
  distinto al incidente original (aquí SÍ conecta el conocimiento, pero lo anula con
  una generalización sin verificar) — invisible para nuestras reglas porque
  `ReasoningHandlingTransformer` ya despoja el CoT antes de que
  `StateAssertionResponseTransformer` vea el contenido. Decisión: no se persigue —
  ver "Decisiones" abajo.
- Sin evidencia de `deferred_denial` (la tarea no forzó una necesidad real de tool
  externa).
- **Bug crítico encontrado**: `[deferred-tools] final-gate: stripped 2 plan-only
  tool(s) (intent=BUILD): EnterPlanMode, ExitPlanMode` para una sesión con
  `model_out=anthropic/kimi-k2` — el fix de ADR-0037 nunca disparaba en producción
  porque `DeferredToolsTransformer` corre ANTES que `ModelRouterTransformer`
  (`request.model` era todavía el alias del cliente, no el modelo enrutado). Fix:
  `FragileModelPlanToolsTransformer`, nuevo transformer registrado después de
  `ModelRouterTransformer` (ver ADR-0037 §4).

## Prueba 2 — Continuar el plan, verificar docs externas, implementar paso 3

**Prompt**: verificar documentación oficial de PyJWT/CVEs recientes (forzando
necesidad real de WebFetch/búsqueda externa) e implementar el paso 3 del plan.

**Hallazgos**:
- Verificación externa real confirmada — contenido HTML/markdown genuino scrapeado
  de una discusión de GitHub (`fastapi/fastapi#11345`) apareciendo en los
  `tool_result`, con CVEs específicos y correctos (CVE-2025-45768, CVE-2024-53861,
  CVE-2026-32597 para PyJWT; CVE-2025-61152, CVE-2024-33663, CVE-2024-33664 para
  python-jose) — no fabricado.
- Cero menciones de negación de tools, cero confusión de nombres de tool.
- **El fix de ADR-0037 confirmado funcionando en producción, sin excepción**: en la
  ventana de logs de esta sesión, 16 pares idénticos de
  `[deferred-tools] final-gate: stripped ... EnterPlanMode, ExitPlanMode` (intents
  READ/SYNTHESIZING/BUILD) seguidos INMEDIATAMENTE por
  `[fragile-model-plan-tools] post-routing guarantee (model=anthropic/kimi-k2):
  added ['ExitPlanMode', 'EnterPlanMode']` — cada strip incorrecto fue recuperado,
  sin ninguna excepción.
- Ningún hallazgo de `deferred_denial`/`no_progress`/`exploration_grounding` en
  logs — sesión limpia.
- Patrón nuevo, no problemático: Kimi dedicó ~1300 líneas de razonamiento interno
  deliberando si debía llamar `ExitPlanMode` dado que el usuario ya había dicho
  "implementa el paso 3" (instrucción ambigua entre "aprobar el plan" y "proceder
  directo"). Terminó tomando la acción correcta (`ExitPlanMode`) en todos los casos
  observados — es ineficiencia de tokens/indecisión, no un error de acción.
- **Gap de verificación real (moderado, no fabricación)**: el plan final incluye
  "CVE-2025-45768 ... disputado por el proveedor" — ese detalle proviene
  únicamente de `Serper [scrape] www.sentinelone.com`, una página que termina con
  `Disclaimer: This content was generated using AI. While we strive for accuracy,
  please verify critical information with official sources.` Kimi sí scrapeó
  `nvd.nist.gov` (la fuente primaria oficial) en la misma sesión, pero para
  `CVE-2025-61152` (python-jose) — **nunca cruzó el detalle "disputado" de
  CVE-2025-45768 contra NVD**, pese a tener acceso directo y demostrado a esa
  fuente primaria en el mismo turno. No es fabricación (el contenido scrapeado es
  real), pero es un hueco de verificación: aceptó un dato específico de una
  fuente que se auto-declara como generada por IA sin cruzarlo contra la fuente
  autoritativa que sí sabía usar. Ninguna de las 3 reglas actuales lo detecta —
  `exploration_grounding` solo mide si el razonamiento usa contenido leído, no si
  esa fuente es primaria/confiable.

## Decisiones tomadas a partir de este análisis

1. **No se construye una regla para el patrón de CoT** ("recordó la regla pero la
   descartó por inducción"). Razón: (a) el contenido de razonamiento ya se elimina
   antes de que el framework de state-assertion lo vea (limitación arquitectónica,
   no un bug puntual); (b) corregir el proceso de pensamiento del modelo es trabajo
   del propio LLM, no del proxy — el framework existente está diseñado para
   verificar acciones/afirmaciones contra la realidad, no para supervisar
   razonamiento libre, que tiene mucho más ruido y menos estructura que acciones
   concretas (tool_use, tool_result, rutas de archivo).
2. **No se construye una regla para la indecisión de ExitPlanMode.** Es un problema
   de eficiencia (tokens desperdiciados), no de corrección — en ambas pruebas
   terminó en la acción correcta. Si se quiere reducir, es un problema de
   claridad del prompt/instrucciones de plan-mode, no de detección proxy-side.
3. **Sí queda anotado como candidato futuro** (no implementado): una regla a nivel
   de acción (no de razonamiento) para "tool call fallido por confusión de nombre
   seguido de reintento con tool similar" (ej. `ExitWorktree` fallando →
   `ExitPlanMode` funcionando poco después) — solo si este patrón se repite en más
   evidencia real; una sola ocurrencia no justifica una regla nueva todavía.
4. **Segundo candidato futuro anotado** (no implementado): verificar CVEs/claims de
   seguridad específicos contra la fuente primaria (NVD) cuando la fuente citada
   se auto-declara como "generated using AI" — patrón real encontrado (CVE-2025-45768
   "disputado por el proveedor" sourceado solo de una página con disclaimer de IA,
   sin cruzar contra NVD pese a tener acceso directo a NVD en la misma sesión). No
   se implementa todavía por la misma razón que el candidato #3: una sola
   ocurrencia no justifica una regla nueva; además requeriría una noción de
   "confiabilidad de fuente" que ninguna regla actual modela.

## Conclusión

ADR-0037 (con el fix de §4) queda verificado en tráfico de producción real, no solo
en tests unitarios — 16/16 recuperaciones correctas observadas, cero recurrencia del
patrón catastrófico del incidente original (escribir código creyendo estar en plan
mode, negar tools existentes, loops sin cambio de estrategia).
