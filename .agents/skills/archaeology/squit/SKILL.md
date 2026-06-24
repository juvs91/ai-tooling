# SQUIT MCP Server - Guía para el Equipo

## Qué es SQUIT MCP

SQUIT es un servidor MCP (Model Context Protocol) que permite a asistentes de IA (Claude, Cursor, etc.) buscar y analizar el código SQL legacy de Deacero.

*En términos simples:* Es como darle a tu asistente de IA acceso a una base de datos inteligente con 5.7 millones de objetos SQL (procedures, views, functions, triggers) enriquecidos con información de 280 aplicaciones de negocio.

---

## Cómo se Creó SQUIT

### Pipeline de Procesamiento de Datos

┌─────────────────────────────────────────────────────────────────────────────┐
│                         PIPELINE DE INGESTA                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. EXTRACCIÓN         2. CHUNKING           3. EMBEDDINGS    4. INDEXADO   │
│  ┌──────────────┐     ┌──────────────┐      ┌────────────┐   ┌───────────┐  │
│  │ SQL Servers  │────►│ Smart        │─────►│ Gemini     │──►│ BigQuery  │  │
│  │ (15+ servers)│     │ Chunker      │      │ Embeddings │   │ Vector    │  │
│  │              │     │              │      │ (768 dims) │   │ Index     │  │
│  └──────────────┘     └──────────────┘      └────────────┘   └───────────┘  │
│        │                    │                     │                │         │
│        ▼                    ▼                     ▼                ▼         │
│  5.7M objetos        Chunks de           Vectores           Búsqueda       │
│  SQL extraídos       6KB máximo          semánticos         < 100ms        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

### Paso 1: Extracción de Código SQL

Se extrajeron objetos SQL de *15+ servidores* de producción:

| Tipo de Objeto | Cantidad | Descripción |
|----------------|----------|-------------|
| PROCEDURE | ~3.2M | Stored procedures |
| VIEW | ~1.5M | Vistas |
| FUNCTION | ~600K | Funciones |
| TRIGGER | ~400K | Triggers |
| *Total* | *~5.7M* | Objetos únicos |

*Fuentes:*

- Servidores de producción (DEAPUENET01, DEAPATMTYNET01, OPEROFIDB02, etc.)
- Servidores de desarrollo y QA
- Bases de datos: Operacion, ARE, CMP, AG, etc.

### Paso 2: Smart Chunking

El código SQL se divide en *chunks inteligentes* optimizados para embeddings:

Configuración del Chunker:
├── max_chunk_size:  6,000 chars   (óptimo para Gemini embeddings)
├── min_chunk_size:    800 chars   (mínimo para contexto semántico)
├── overlap_size:      300 chars   (continuidad entre chunks)
└── mega_threshold: 1,000,000 chars (objetos gigantes = tratamiento especial)

*Estrategias de chunking:*

1. *Por estructura semántica* - Respeta BEGIN/END, GO separators
2. *Preservar contexto* - Cada chunk incluye metadata del objeto padre
3. *Separar funciones* - Procedures grandes se dividen por bloques lógicos
4. *Clasificación automática* - Detecta tipo: complex_query, stored_procedure, dml, ddl

### Paso 3: Generación de Embeddings

Cada chunk se convierte en un *vector de 768 dimensiones* usando Gemini:

┌─────────────────────────┐         ┌──────────────────────────────┐
│ "SELECT * FROM          │         │ [0.023, -0.156, 0.892, ...,  │
│  ClientesMaster         │  ──────►│  0.041, -0.234, 0.567, ...,  │
│  WHERE activo = 1"      │         │  0.123, -0.089]              │
└─────────────────────────┘         └──────────────────────────────┘
       Chunk SQL                          Vector 768 dimensiones

- *Modelo:* gemini-embedding-001
- *Dimensiones:* 768
- *Velocidad:* ~50,000 chunks/hora en batch

### Paso 4: Enriquecimiento con Catálogo de Aplicaciones

El *catálogo de 280 aplicaciones* enriquece cada objeto con contexto de negocio:

| Sistema | Descripción | Dominio | Proceso E2E |
|---------|-------------|---------|-------------|
| ARE | Precios mínimos | ventas | Cotización → Facturación |
| CMP | Inventarios cíclicos | inventario | Conteo → Ajuste |
| AG | Agroindustrial | produccion | Siembra → Cosecha |
| ... | ... | ... | ... |

Esto permite:

- Mapear términos de negocio → código SQL específico
- Clasificar automáticamente por dominio
- Entender el proceso end-to-end de cada sistema

### Paso 5: Indexado Vectorial en BigQuery

Todo se almacena en *BigQuery* con índice vectorial nativo:

sql
-- Estructura de la tabla de embeddings
chunk_embeddings:
├── chunk_id           STRING     -- ID único del chunk
├── parent_object_id   STRING     -- SERVER|DB|schema|object_name
├── object_name        STRING     -- Nombre del objeto
├── object_type        STRING     -- PROCEDURE, VIEW, FUNCTION, TRIGGER
├── business_domain    STRING     -- ventas, inventario, finanzas, etc.
├── semantic_summary   STRING     -- Resumen semántico generado
├── chunk_content      STRING     -- Código SQL del chunk
├── embedding          FLOAT64[]  -- Vector de 768 dimensiones
├── complexity_score   FLOAT64    -- Score de complejidad (1-10)
└── semantic_tags      STRING[]   -- Tags: [SELECT, JOIN, INSERT, etc.]

-- Índice vectorial para búsqueda <100ms
VECTOR INDEX (distance_type='COSINE', index_type='IVF')

### Paso 6: Cálculo de Dependencias

Se analizan las *referencias cruzadas* entre objetos:

Análisis de Dependencias:
├── Busca menciones de tablas/procedures en el código
├── Clasifica tipo de uso: READ (SELECT), WRITE (INSERT/UPDATE), REFERENCE
├── Calcula criticidad: CRITICAL, MEDIUM, LOW
└── Almacena para consultas de impacto rápidas

---

## Arquitectura del Servidor MCP

┌─────────────────┐     HTTPS + API Key     ┌──────────────────────┐
│  Claude Code    │ ◄──────────────────────► │  squit-mcp.deacero.us │
│  Cursor         │                          │  (nginx + SSL)        │
│  Claude Desktop │                          └──────────┬─────────────┘
└─────────────────┘                                     │
                                                        ▼
                                              ┌──────────────────────┐
                                              │  Docker Container    │
                                              │  (FastMCP Server)    │
                                              └──────────┬───────────┘
                                                         │
                                                         ▼
                                              ┌──────────────────────┐
                                              │  Google BigQuery     │
                                              │  + Vector Search     │
                                              │  (5.7M objetos SQL)  │
                                              └──────────────────────┘

## Herramientas Disponibles

El MCP expone 5 herramientas que tu asistente de IA puede usar:

### 1. squit_search - Búsqueda Semántica

Busca código SQL por significado, no solo por texto exacto.

Ejemplo: "cálculo de inventario kayak"
Resultado: Encuentra procedures relacionados aunque no contengan esas palabras exactas

*Parámetros:*

- query: Búsqueda en lenguaje natural
- business_domains: Filtrar por dominio (ventas, inventario, finanzas, etc.)
- object_types: Filtrar por tipo (PROCEDURE, VIEW, FUNCTION, TRIGGER)
- limit: Máximo resultados (1-50)

### 2. squit_get_code - Obtener Código Completo

Recupera el código SQL completo de un objeto.

Ejemplo: Obtener el código de "ARECalculoPreciosMinimosMx"

*Parámetros:*

- parent_object_id: ID del objeto (formato: SERVER|DB|schema|object_name)

### 3. squit_dependencies - Análisis de Dependencias

Encuentra qué objetos SQL dependen de uno específico.

Ejemplo: "¿Qué código usa la tabla ClientesMaster?"
Resultado: Lista de procedures, views, etc. que leen o escriben esa tabla

*Parámetros:*

- object_name: Nombre del objeto a analizar
- limit: Máximo dependencias (default: 20)

### 4. squit_impact - Análisis de Impacto

Genera un reporte de riesgo antes de modificar un objeto.

Ejemplo: "¿Qué pasa si modifico sp_CalcularInventario?"
Resultado: Reporte con objetos afectados, nivel de riesgo, recomendaciones

*Parámetros:*

- object_name: Nombre del objeto a analizar

### 5. squit_read_chunk - Leer Fragmento

Lee un fragmento específico de código (útil para objetos muy grandes).

*Parámetros:*

- chunk_id: ID del chunk

## Configuración

### Para Claude Code / Claude Desktop

Agregar a ~/.claude.json en la sección mcpServers:

json
{
  "mcpServers": {
    "squit": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://squit-mcp.deacero.us/mcp",
        "--header",
        "X-API-Key: TU_API_KEY_AQUI"
      ],
      "env": {}
    }
  }
}

### Para Cursor

Agregar a la configuración de MCP en Cursor Settings > MCP:

json
{
  "squit": {
    "command": "npx",
    "args": [
      "-y",
      "mcp-remote",
      "https://squit-mcp.deacero.us/mcp",
      "--header",
      "X-API-Key: TU_API_KEY_AQUI"
    ]
  }
}

*Importante:* Después de configurar, reinicia la aplicación.

## API Keys

Cada miembro del equipo tiene asignada una API key única:

| # | Nombre | Asignado a |
|---|--------|------------|
| 01 | squit-mcp-key-01 | [Karim] |
| 02 | squit-mcp-key-02 | [Disponible] |
| 03 | squit-mcp-key-03 | [Disponible] |
| 04 | squit-mcp-key-04 | [Disponible] |
| 05 | squit-mcp-key-05 | [Disponible] |
| 06 | squit-mcp-key-06 | [Disponible] |
| 07 | squit-mcp-key-07 | [Disponible] |
| 08 | squit-mcp-key-08 | [Disponible] |
| 09 | squit-mcp-key-09 | [Disponible] |
| 10 | squit-mcp-key-10 | [Disponible] |

*Solicita tu API key al administrador.*

## Ejemplos de Uso

Una vez configurado, puedes preguntarle a tu asistente de IA:

### Búsqueda de código
>
> "Busca procedures relacionados con cálculo de precios mínimos"

### Análisis de impacto
>
> "¿Qué impacto tendría modificar la tabla InventarioMaster?"

### Obtener código
>
> "Muéstrame el código del procedure ARECalculoPreciosMinimosMx"

### Análisis de dependencias
>
> "¿Qué objetos dependen de la view vw_VentasDiarias?"

## Dominios de Negocio

Los objetos SQL están clasificados en dominios:

- ventas - Procesos de venta y facturación
- inventario - Control de inventarios
- finanzas - Contabilidad y finanzas
- produccion - Procesos de manufactura
- logistica - Embarques y distribución
- compras - Adquisiciones y proveedores

## Límites y Restricciones

- *Rate limit:* 120 requests/minuto por API key
- *Burst:* 50 requests simultáneos
- *Resultados:* Máximo 50 por búsqueda

## Troubleshooting

### Error 503 Service Unavailable

- Reinicia tu cliente (Claude Code, Cursor)
- Espera 1 minuto si excediste el rate limit

### Error 401 Unauthorized

- Verifica que tu API key esté correcta
- Asegúrate de incluir el header X-API-Key

### MCP no conecta

- Verifica tu conexión a internet
- Prueba: curl <https://squit-mcp.deacero.us/health>
- Debe responder: {"status":"healthy","service":"squit-mcp"}

## Soporte

- *Servidor:* node-worker-1 (158.69.247.40)
- *URL:* <https://squit-mcp.deacero.us>
- *Health check:* <https://squit-mcp.deacero.us/health>
- *Contacto:* [Karim]
