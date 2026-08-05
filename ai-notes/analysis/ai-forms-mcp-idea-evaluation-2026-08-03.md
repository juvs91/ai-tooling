# Evaluación: MCP para generación agéntica de formularios/contratos

**Fecha:** 2026-08-03
**Propósito:** brief autocontenido para arrancar una sesión nueva (con Kimi o con Claude)
sin necesitar el historial de esta conversación.

## 1. La idea, tal como la planteó el usuario

> Un MCP que ayude a generar formas/contratos de manera agéntica, analizando endpoints y
> contratos de las bases de datos — parsear/analizar YAMLs/JSON de OpenAPI, de FastAPI, de
> SQLAlchemy o Pydantic, para conversar con un usuario y regresarle una forma que tiene que
> llenar, y que esa forma mande a llamar la API o inserte en el modelo de datos
> correspondiente. Foco actual: APIs para el tema de validaciones.

En corto: **derivar un formulario conversacional directamente del contrato real de un
backend (no inventado por prompt), usar esas mismas reglas de validación como fuente de
verdad, y cerrar el loop escribiendo a la API/DB real** — no un formulario genérico
desconectado del sistema.

## 2. Evaluación de la idea — qué es buena decisión y qué no

### Lo que está bien pensado

- **Usar el contrato existente como fuente de verdad de validación** (en vez de que el LLM
  invente reglas) es la decisión correcta. Es exactamente el mismo principio que motivó
  ADR-0031/0033 esta sesión: no confiar en que el LLM "sepa" algo, verificar contra
  evidencia real (el schema real, no una alucinación de schema).
- **Priorizar APIs (FastAPI/OpenAPI) sobre parseo directo de modelos SQLAlchemy** — foco
  correcto, y de hecho es más fácil de lo que el usuario cree (ver sección 3).
- El framing MCP tiene sentido **si el consumidor final es un desarrollador/operador
  trabajando con un cliente agéntico** (Claude Code, Claude Desktop, Cursor) — ahí un MCP
  que exponga "generame un formulario válido para POST /endpoint X" es una herramienta de
  productividad real y con precedente directo (ver FormHug abajo).

### Lo que hay que resolver antes de programar nada

1. **¿Quién llena el formulario?** Esto no quedó explícito y cambia todo el diseño:
   - Si es un **desarrollador/operador usando un agente** (ej. Claude Code ayudando a armar
     un payload de prueba) → MCP es la arquitectura correcta.
   - Si es un **usuario de negocio no técnico** (RH, finanzas, alguien llenando un formulario
     real de la operación) → MCP es la herramienta equivocada. MCP conecta agentes con
     tools dentro de un cliente agéntico (Claude Desktop/Code) — no es un mecanismo para
     servir una UI web a un usuario final. Ahí se necesitaría una app/web normal (que
     puede usar un LLM por debajo, pero la superficie de cara al usuario no es MCP).
   - Dado el contexto de Grupo Deacero (usuarios de perfiles diversos, muchos no técnicos),
     esta pregunta es la primera que hay que resolver en la nueva sesión, no asumirla.

2. **La capa "conversar con el usuario" es la parte más nueva y la más riesgosa.**
   Traducir JSON Schema → formulario ya es un problema resuelto (ver sección 3): existen
   librerías maduras (react-jsonschema-form, uniforms, Formily) que renderizan formularios
   directo desde JSON Schema, sin LLM de por medio. Lo genuinamente nuevo de esta idea es
   la conversación progresiva/agéntica en vez de un formulario estático. Pero es también
   el punto más propenso a alucinación: un LLM completando/interpretando campos de un
   schema complejo (anidado, con arrays, con validadores custom) puede inventar campos que
   no existen o mal-interpretar restricciones. Esto es el MISMO problema de grounding que
   se trabajó toda esta sesión (ADR-0031/0033) — vale la pena diseñar, desde el día uno,
   una verificación explícita de "el formulario que le mostré al usuario, ¿coincide
   campo por campo con el schema real?" antes de aceptar la respuesta, en vez de confiar
   en que el LLM lo hizo bien.

3. **Techo real de "las validaciones de la API son la fuente de verdad":** OpenAPI/JSON
   Schema expresa bien restricciones de campo simples (tipo, rango, regex, enum, required).
   NO expresa validadores custom de Pydantic (`@field_validator`/`@model_validator`) ni
   reglas cruzadas entre campos que solo existen como código Python. El formulario podrá
   reflejar fielmente la validación *sintáctica*, pero no capturará automáticamente reglas
   de negocio complejas — esas seguirán fallando solo cuando la API las rechace en submit.
   Vale la pena aceptar esto como límite conocido desde el diseño, no descubrirlo tarde.

## 3. Qué tan difícil es cada fuente de "contrato" — de más fácil a más difícil

| Fuente | Dificultad | Por qué |
|---|---|---|
| **FastAPI/OpenAPI** | Baja | FastAPI ya genera `/openapi.json` completo (tipos, constraints, requeridos) a partir de los modelos Pydantic de cada endpoint. **No hace falta parsear código Python ni YAMLs a mano** — alcanza con consumir el JSON Schema que FastAPI ya expone. Esto simplifica muchísimo el "parser/analizador" que el usuario imaginaba: gran parte de ese trabajo ya está hecho por el framework. |
| **Pydantic standalone** (sin FastAPI) | Baja-media | Pydantic expone `.model_json_schema()` directamente — mismo principio, sin necesitar FastAPI corriendo. |
| **SQLAlchemy models** | Media-alta | No hay un export estándar tipo OpenAPI. Requiere introspección propia vía `Base.metadata.tables` (columnas, tipos, nullable, FKs, constraints) o una librería de terceros. Además, las constraints de DB (NOT NULL, CHECK) no siempre capturan las reglas de negocio de la capa de aplicación. |
| **Introspección directa de base de datos** (`information_schema`) | Alta | Es la fuente más pobre semánticamente: se pierden validaciones que solo existen en código de aplicación (Pydantic/FastAPI), quedando solo constraints de esquema SQL. |

**Recomendación concreta**: arrancar el MVP apuntando *solo* a FastAPI/OpenAPI (la fuente
más barata y más completa), y tratar SQLAlchemy/DB-directo como fase 2 — no las 4 fuentes
a la vez desde el día uno.

## 4. Evaluación de los links que se pasaron

| Link | Qué es | Utilidad real |
|---|---|---|
| [theresanaiforthat.com/ai/formhug](https://theresanaiforthat.com/ai/formhug/) | Devolvió 403 al fetch directo — se investigó vía búsqueda web en su lugar. | Ver hallazgo completo abajo — **es el prior art más relevante de los 4 links**. |
| [claude-world.com tutorial de deferred tools/ToolSearch](https://claude-world.com/tutorials/s30-deferred-tools-and-toolsearch/) | Tutorial de terceros (no oficial de Anthropic) sobre deferred tools y ToolSearch en Claude Code. | Técnicamente coherente y bien razonado, sin inconsistencias internas evidentes — pero es contenido de terceros sin verificación oficial citada, y usa fechado prospectivo ("July 8, 2026") que sugiere es contenido educativo/especulativo, no spec oficial congelada. Sirve como introducción legible, pero para cualquier detalle que importe (formato exacto de queries, límites, nombres de tools) hay que confirmar contra la doc oficial de Anthropic — que sí se pudo traer completa (ver siguiente fila). |
| [platform.claude.com — Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) | Documentación oficial de Anthropic, API cruda (no Claude Code). | **La más valiosa de los 4.** Trae el mecanismo real completo: dos variantes (`tool_search_tool_regex_20251119` con regex de Python, `tool_search_tool_bm25_20251119` con lenguaje natural), el flag `defer_loading: true` por tool, límites exactos (10,000 tools deferred máx, 5 resultados por búsqueda, 200/500 caracteres de query), y el formato exacto de request/response. Esto es lo que hay que citar si se necesita precisión, no el tutorial de terceros. |
| [code.claude.com — Tools Reference](https://code.claude.com/docs/en/tools-reference) | Documentación oficial de Claude Code (la capa harness, no la API cruda). | Confirma que `ToolSearch` es una tool real y documentada de Claude Code, ligada a que "tool search" esté habilitado, con una tool hermana `WaitForMcpServers` que solo aparece cuando tool search está *deshabilitado*. Útil para entender la capa CLI/harness sobre la API cruda del link anterior. |

### Hallazgo relevante sobre FormHug (vía búsqueda web, no fetch directo)

**FormHug ya hace una versión de exactamente esta idea** — con una diferencia clave que
vale la pena que el usuario conozca antes de decidir si construir esto desde cero tiene
sentido:

- FormHug tiene un **servidor MCP propio** que deja a agentes (Claude, Cursor, Manus,
  Windsurf) crear formularios, editar campos, leer respuestas y enviar entradas — MCP,
  igual que la idea del usuario.
- Pero FormHug genera formularios **genéricos a partir de un prompt** (registro, encuesta,
  quiz), alojados en su propia plataforma, sin ninguna noción de "derivar el formulario del
  contrato real de tu backend" (OpenAPI/SQLAlchemy/Pydantic).
- **La diferenciación real de la idea del usuario no es "MCP + formularios" (eso ya existe)
  — es "el formulario está *anclado* al contrato real de un sistema existente propio, y el
  submit escribe directo a ese sistema, no a un storage de terceros."** Esa es la parte que
  vale la pena validar que nadie más esté haciendo, no la idea de MCP-para-formularios en
  general.

## 5. Preguntas abiertas para arrancar la próxima sesión

1. ¿Quién llena el formulario — un desarrollador/operador vía agente, o un usuario de
   negocio no técnico? (determina si MCP es la arquitectura correcta o no)
2. ¿Empezamos por FastAPI/OpenAPI únicamente (recomendado, menor esfuerzo) o hace falta
   SQLAlchemy/DB-directo desde el MVP?
3. ¿Cómo se verifica que el formulario generado por el agente coincide de verdad con el
   schema real antes de mostrárselo al usuario? (mismo problema de grounding que ADR-0031/0033)
4. ¿Vale la pena revisar FormHug de cerca (su MCP server es público) antes de diseñar desde
   cero, aunque sea para no reinventar la parte de "MCP + formulario" que ya resolvieron?
