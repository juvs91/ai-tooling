# Contexto de Tickets Jira

Obtienes informacion de Jira para el proyecto ARP (Arquitectura de Precios).

Busqueda: $ARGUMENTS

## Identificar Tipo de Busqueda

Si $ARGUMENTS es un numero de ticket (ej: ARP-495, ARP-180):
- Usar busqueda por ticket especifico

Si $ARGUMENTS es un termino (ej: "precio", "cascada", "integracion"):
- Usar busqueda JQL

## Busqueda por Ticket Especifico

Usar `mcp__atlassian__jira_get_issue` con:
- issue_key: "$ARGUMENTS"

Extraer:
- Titulo (summary)
- Estado (status)
- Prioridad (priority)
- Asignado (assignee)
- Reporter
- Descripcion completa
- Subtareas
- Links a otros tickets
- Comentarios recientes

## Busqueda por Termino

Usar `mcp__atlassian__jira_search` con JQL:
```
project = ARP AND (summary ~ "$ARGUMENTS" OR description ~ "$ARGUMENTS") ORDER BY updated DESC
```

Limitar a 20 resultados.

## Tickets Criticos Conocidos

### Bugs Activos de Alta Prioridad
| Ticket | Titulo | Prioridad | Estado |
|--------|--------|-----------|--------|
| ARP-495 | Integracion ARP con Sistema Comercial no funciona | MUY ALTA | QA Environment |
| ARP-505 | Inconsistencia de datos entre SC y ARP | ALTA | CODE |
| ARP-489 | Sincronizacion de datos desactualizada | ALTA | Pendiente |
| ARP-494 | Performance degradada en Tab 1 | MEDIA | Code Review |
| ARP-493 | Latencia alta en cotizaciones grandes | MEDIA | Backlog |

### Epicas Principales (20 total)
| Ticket | Titulo | Estado | Progreso |
|--------|--------|--------|----------|
| ARP-169 | Calculo del Precio Base | EN PROGRESO | 75% |
| ARP-180 | Calculo del Precio Final | POR HACER | VENCIDA |
| ARP-8 | Integraciones | POR HACER | 0% |
| ARP-4 | Alertas y Notificaciones | POR HACER | 0% |
| ARP-3 | Auditoria | POR HACER | 0% |
| ARP-262 | Feature Toggle | CODE | 50% |
| ARP-427 | Analizar Info en Looker | POR HACER | 0% |

### Historias Completadas (RELEASE)
- ARP-77: Proporcionar datos del Precio Base
- ARP-25: Calcular Precio Base
- ARP-137: Modulo de cargos y descuentos
- ARP-132: Obtencion de cargos y descuentos
- ARP-144: Configuracion de condiciones de descuento

## Equipo del Proyecto

| Nombre | Rol | Area |
|--------|-----|------|
| Elaine Da Costa Silva | Coordinadora | Gestion |
| Erving Castillo Ramos | Lead Tecnico | Backend |
| Julio Cesar Valdez | Desarrollador | Integracion |
| Diana Basilio Beltran | QA | Testing |
| Eduardo Cantu Trevino | Desarrollador | Calculos |
| Luis Eduardo Hernandez | Desarrollador | Frontend |

## Busqueda en Confluence

Si necesitas documentacion relacionada, usar `mcp__atlassian__confluence_search`:
```
space = ARQ AND text ~ "$ARGUMENTS"
```

Paginas relevantes:
- Arquitectura de Precios - Vision General
- Reglas de Negocio
- Validaciones del Sistema
- Integracion con Sistema Comercial

## Formato de Respuesta

### Para ticket especifico:
```
## [ARP-XXX] [Titulo]

**Estado**: [estado]
**Prioridad**: [prioridad]
**Tipo**: [Bug/Story/Epic/Task]
**Asignado**: [nombre]
**Reporter**: [nombre]
**Sprint**: [sprint actual]
**Fecha creacion**: [fecha]
**Ultima actualizacion**: [fecha]

### Descripcion
[descripcion completa]

### Criterios de Aceptacion
[lista si existen]

### Subtareas
| Subtarea | Estado |
|----------|--------|
| [titulo] | [estado] |

### Links Relacionados
- Bloquea: [tickets]
- Bloqueado por: [tickets]
- Relacionado: [tickets]

### Comentarios Recientes
[ultimos 3 comentarios]
```

### Para busqueda:
```
## Resultados para "[termino]"

Encontrados [N] tickets:

| Ticket | Titulo | Estado | Prioridad |
|--------|--------|--------|-----------|
| ARP-XXX | [titulo] | [estado] | [prioridad] |

### Tickets Mas Relevantes
[descripcion breve de los 3 mas relevantes]
```

## Degradacion Elegante

Si Atlassian MCP no responde:

1. Informar: "MCP de Atlassian no disponible"
2. Buscar en documentacion local:
   - `docs/arquitectura-precios/05-estado-proyecto/tickets-detallados.md`
   - `docs/arquitectura-precios/05-estado-proyecto/epicas.md`
   - `docs/arquitectura-precios/05-estado-proyecto/bugs-conocidos.md`
3. Mostrar tickets conocidos de la tabla de arriba
4. Indicar que la informacion puede no estar actualizada
5. Sugerir acceder directamente: https://deacero.atlassian.net/jira/software/c/projects/ARP/
