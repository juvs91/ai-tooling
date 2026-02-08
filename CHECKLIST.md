# AI-Tooling Implementation Checklist

> Este checklist se actualiza conforme se implementan las features.
> Última actualización: 2026-02-06

---

## 🔴 Crítico (Bloquea onboarding)

- [x] **cc-proxy-init path fix** - Override files now created in `cloud-provider-ymls/`
- [x] **README quick start** - Pendiente crear sección de 5 minutos
- [x] **Templates con ejemplos**
  - [x] `AI_CONTEXT.example.md` - Ejemplo completo OAuth2
  - [x] `AI_PLAN.example-detailed.md` - Con multihop grounding
  - [x] `AI_LEARNING.template.md` - Para aprendizajes iterativos
- [ ] **Demo script** - `demo/example-workflow.sh`

---

## 🟡 Importante (Mejora experiencia)

- [x] **Token counting endpoint** - `/v1/messages/count_tokens` implementado
- [x] **Health checks**
  - [x] `/health` endpoint en proxy
  - [x] `cc-health` script
- [x] **--cloud flag** - Agregado a cc-chat, cc-scan, cc-plan
- [x] **TOOL_ALLOWLIST='*'** - Wildcard para permitir todos los tools
- [x] **Tests**
  - [x] `tests/test_utils.py` - Utils functions
  - [x] `tests/test_server.py` - Server endpoints
  - [x] `tests/test_router.py` - Router and mapper
  - [x] `tests/conftest.py` - Shared fixtures

---

## 🟢 Opcional (Nice to have)

- [ ] **Video tutorial** - Walkthrough de 10 minutos
- [ ] **API reference** - Documentación OpenAPI
- [ ] **VS Code extension** - Integración nativa
- [ ] **Métricas** - Dashboard de uso/costos

---

## Scripts disponibles

| Script | Descripción | --cloud |
|--------|-------------|---------|
| `cc-chat` | Chat interactivo o single-shot | ✅ |
| `cc-scan` | Análisis de archivos | ✅ |
| `cc-plan` | Generación de planes | ✅ |
| `cc-agent-cloud` | Ejecución con tools | N/A (siempre cloud) |
| `cc-proxy-up` | Iniciar proxy | N/A |
| `cc-proxy-init` | Configurar provider | N/A |
| `cc-health` | Verificar estado proxy | N/A |

---

## Providers soportados

| Provider | Config file | Estado |
|----------|-------------|--------|
| Z.AI | `cloud.zai.env` | ✅ Funcional |
| Groq | `cloud.groq.env` | ✅ Funcional |
| Gemini | `cloud.gemini.env` | ✅ Funcional |
| Ollama | `cloud.ollama.env` | ✅ Funcional |
| OpenAI | Custom | ✅ Funcional |
| Anthropic | Direct | ✅ Funcional |

---

## Guardrails implementados

| Guardrail | Ubicación | Estado |
|-----------|-----------|--------|
| `BASE_GUARD_SYSTEM` | `proxy/proxy.py` | ✅ Activo |
| `TOOL_ALLOWLIST` | `utils/utils.py` | ✅ Con wildcard |
| `MAX_INPUT_TOKENS` | `proxy/proxy.py` | ✅ Configurable |
| Provider caps | `proxy/proxy.py` | ✅ Groq/Ollama |
| Plan REVIEWED check | `cc-agent-cloud` | ✅ Bloquea sin review |

---

## Templates disponibles

| Template | Uso | Ejemplo |
|----------|-----|---------|
| `AI_CONTEXT.template.md` | Contexto del ticket | `AI_CONTEXT.example.md` |
| `AI_PLAN.template.md` | Plan básico | `AI_PLAN.example-detailed.md` |
| `AI_LEARNING.template.md` | Aprendizajes | - |
| `GUARDRAILS.template.md` | Reglas de seguridad | - |

---

## Quick Start (5 minutos)

```bash
# 1. Clonar e instalar
git clone <repo>
cd ai-tooling

# 2. Configurar provider (ejemplo: Z.AI)
./scripts/cc-proxy-init.sh zai
# Editar profile-envs/cloud.zai.env con tu API key

# 3. Iniciar proxy
./scripts/cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml

# 4. Verificar
./scripts/cc-health

# 5. Usar
./scripts/cc-chat "Hola, funcionas?"
```

---

## Workflow completo

```
┌─────────────────────────────────────────────────────────────┐
│                     WORKFLOW RECOMENDADO                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Crear AI_CONTEXT.md                        [MANUAL]     │
│     └─> Define ticket, inputs, outputs, guardrails         │
│                                                             │
│  2. cc-scan <archivos>                         [LOCAL]      │
│     └─> Genera *.analysis.md (tools OFF)                   │
│                                                             │
│  3. cc-plan                                    [LOCAL]      │
│     └─> Genera AI_PLAN.md (STATUS: DRAFT)                  │
│                                                             │
│  4. Revisar AI_PLAN.md                         [MANUAL]     │
│     └─> Cambiar STATUS: DRAFT → REVIEWED                   │
│     └─> Agregar Reviewed-by: <nombre>                      │
│                                                             │
│  5. cc-agent-cloud                             [CLOUD]      │
│     └─> Valida REVIEWED, ejecuta con tools                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Notas de implementación

### 2026-02-06
- ✅ Fix cc-proxy-init path bug
- ✅ Implementado /v1/messages/count_tokens
- ✅ Implementado /health endpoint
- ✅ Creado cc-health script
- ✅ Agregado --cloud flag a scripts
- ✅ Implementado TOOL_ALLOWLIST='*' wildcard
- ✅ Creados tests con pytest
- ✅ Creados templates de ejemplo

### Pendiente
- [ ] Demo script con workflow completo
- [ ] Video tutorial
- [ ] Integración VS Code nativa
