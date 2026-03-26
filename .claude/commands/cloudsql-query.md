# Consulta de Datos en CloudSQL (prices)

Consultas sobre datos de la API de Arquitectura de Precios en Cloud SQL (base `prices`, proyectos `wc-prj-dev/qa/prod`).

Parametros: $ARGUMENTS

## Prerequisitos

### 1. Ambiente activo

El ambiente se controla con `WPC_ENV` en `.cloudsql-env`:
```
WPC_ENV=dev   # wc-prj-dev
WPC_ENV=qa    # wc-prj-qa
WPC_ENV=prod  # wc-prj-prod
```

Para ver que ambiente esta activo:
```bash
grep WPC_ENV .cloudsql-env
```

Para cambiar ambiente: editar `.cloudsql-env` → cambiar `WPC_ENV` → **Reload Window** en VS Code.

### 2. Tunel SSH activo (puerto 5432)

Verificar:
```bash
lsof -i :5432
```

Si no esta activo, iniciar segun el ambiente:
```bash
# DEV
gcloud compute ssh cloudsql-proxy --project wc-prj-dev --zone us-central1-a -- -NL 5432:localhost:5432

# QA
gcloud compute ssh cloudsql-proxy --project wc-prj-qa --zone us-central1-a -- -NL 5432:localhost:5432

# PROD
gcloud compute ssh cloudsql-proxy --project wc-prj-prod --zone us-central1-a -- -NL 5432:localhost:5432
```

### 3. Verificar conexion

Usar `mcp__cloudsql__query_tool`:
```sql
SELECT current_database(), current_user, version();
```

## Diferencia con AlloyDB

| Aspecto | AlloyDB (ODS) | CloudSQL (prices) |
|---------|---------------|-------------------|
| Puerto | 5435 | 5432 |
| Base de datos | ods | prices |
| Proyecto GCP | dea-ods-prj-prod | wc-prj-dev/qa/prod |
| Contenido | Datos legacy replicados | API de Arquitectura de Precios |
| MCP Tool | `mcp__alloydb__query_tool` | `mcp__cloudsql__query_tool` |
| Ambiente | Siempre produccion | dev / qa / prod |

## Tablas Principales (base `prices`)

### Catalogo de Articulos / Productos
```sql
-- Buscar productos
SELECT id, sku, name, unit_of_measure
FROM products
WHERE name ILIKE '%[BUSQUEDA]%'
LIMIT 20;
```

### Listas de Precios
```sql
-- Listas activas
SELECT id, name, currency, valid_from, valid_to, status
FROM price_lists
WHERE status = 'active'
  AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
ORDER BY valid_from DESC;

-- Precios de un producto en todas las listas activas
SELECT
    pl.name as lista,
    pi.product_id,
    pi.price,
    pi.currency,
    pl.valid_from,
    pl.valid_to
FROM price_items pi
JOIN price_lists pl ON pi.price_list_id = pl.id
WHERE pi.product_id = [ID_PRODUCTO]
  AND pl.status = 'active'
ORDER BY pl.valid_from DESC;
```

### Clientes y Descuentos
```sql
-- Descuentos por cliente
SELECT
    cd.customer_id,
    cd.discount_type,
    cd.discount_value,
    cd.valid_from,
    cd.valid_to
FROM customer_discounts cd
WHERE cd.customer_id = [ID_CLIENTE]
  AND (cd.valid_to IS NULL OR cd.valid_to >= CURRENT_DATE)
ORDER BY cd.discount_type;
```

### Ordenes / Cotizaciones
```sql
-- Ordenes recientes
SELECT
    o.id,
    o.customer_id,
    o.status,
    o.total_amount,
    o.currency,
    o.created_at
FROM orders o
ORDER BY o.created_at DESC
LIMIT 20;

-- Detalle de una orden
SELECT
    oi.order_id,
    oi.product_id,
    p.name as producto,
    oi.quantity,
    oi.unit_price,
    oi.discount,
    oi.total_price
FROM order_items oi
JOIN products p ON oi.product_id = p.id
WHERE oi.order_id = [ID_ORDEN];
```

## Queries de Diagnostico

### Ver schema real
```sql
-- Tablas disponibles
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

-- Columnas de una tabla
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = '[TABLA]'
ORDER BY ordinal_position;
```

### Verificar datos recientes
```sql
-- Ultima actividad por tabla (si tienen timestamps)
SELECT
    'orders' as tabla,
    MAX(created_at) as ultimo_registro,
    COUNT(*) as total
FROM orders;
```

## Consideraciones por Ambiente

| Ambiente | Uso | Precaucion |
|----------|-----|------------|
| `dev` | Desarrollo y pruebas | Datos de prueba, puede estar desactualizado |
| `qa` | Validacion | Datos similares a produccion |
| `prod` | Produccion | SOLO LECTURA — nunca modificar datos |

> **IMPORTANTE**: En produccion, solo ejecutar queries `SELECT`. Nunca `INSERT`, `UPDATE`, `DELETE` en prod.

## Formato de Respuesta

```
## Resultado CloudSQL ([WPC_ENV])

### Query Ejecutada
```sql
[query]
```

### Resultados
| col1 | col2 | col3 |
|------|------|------|
| ... | ... | ... |

**Total registros**: N
**Ambiente**: [dev / qa / prod]
**Observaciones**: [datos faltantes, valores inesperados, diferencias vs AlloyDB]
```

## Degradacion

Si CloudSQL no esta disponible:
1. Verificar tunel: `lsof -i :5432`
2. Verificar `WPC_ENV` en `.cloudsql-env`
3. Re-autenticar GCP: `gcloud auth login`
4. Reiniciar tunel con el comando del ambiente correspondiente
5. Para datos equivalentes en AlloyDB usar `mcp__alloydb__query_tool` (datos replicados pueden estar disponibles)
