# Consulta de Datos en AlloyDB (ODS)

Consultas sobre datos de precios, listas, descuentos y ventas en AlloyDB (base `ods`).

Parametros: $ARGUMENTS

## Prerequisitos

Tunel SSH activo en puerto 5435:
```bash
lsof -i :5435
```

Si no esta activo:
```bash
gcloud compute ssh wpc-alloydb-proxy \
    --project dea-ods-prj-prod \
    --zone us-central1-a \
    -- -NL 5435:localhost:5432
```

## Como Ejecutar Queries

Usar `mcp__alloydb__query_tool` con la query SQL como parametro.

Verificar conexion:
```sql
SELECT current_database(), current_user, version();
```

## Schemas Principales

| Schema | Contenido |
|--------|-----------|
| `public` | Tablas principales ODS |
| `VtaSch` | Ventas y fabricaciones |
| `dbo_*` | Tablas replicadas desde SQL Server |

## Tablas Frecuentes

### Precios y Listas
```sql
-- Listas de precio activas
SELECT * FROM "VtaTraListaPrecio" WHERE FecFin IS NULL LIMIT 20;

-- Detalle de precios por articulo
SELECT * FROM "MSWTraListaPrecioDetDEAUSA"
WHERE ClaListaPrecio = [ID_LISTA]
ORDER BY ClaArticulo
LIMIT 100;

-- Descuentos vigentes
SELECT * FROM "VtaTraListaDescuento"
WHERE FecFin IS NULL
ORDER BY FecIni DESC
LIMIT 50;
```

### Fabricaciones / Ordenes de Venta
```sql
-- Fabricaciones recientes
SELECT
    IdFabricacion,
    ClaAnioMes,
    ClaEscenario,
    FecFabricacion,
    ClaTipoEmbarque,
    TonsVenta
FROM "VtaSch_VtaTraFabricacion"
WHERE ClaAnioMes >= [YYYYMM]
ORDER BY FecFabricacion DESC
LIMIT 50;

-- Detalle de una fabricacion
SELECT * FROM "VtaSch_VtaTraFabricacionDet"
WHERE IdFabricacion = [ID]
ORDER BY NumeroRenglon;
```

### Articulos
```sql
-- Buscar articulo
SELECT ClaArticulo, NomArticulo, PesoTeoricoKgs
FROM dbo_ArtCatArticulo
WHERE NomArticulo ILIKE '%[BUSQUEDA]%'
LIMIT 20;
```

### Clientes y Cuentas
```sql
-- Buscar cliente
SELECT ClaCliente, ClaCuenta, NomCliente
FROM dbo_CxcCatCliente
WHERE NomCliente ILIKE '%[BUSQUEDA]%'
LIMIT 20;
```

## Convencion de Nombres

| Prefijo | Significado |
|---------|-------------|
| `VtaTra*` | Transaccional de ventas |
| `VtaCat*` | Catalogos de ventas |
| `MSWTra*` | Tablas MCSW_ERP replicadas |
| `dbo_*` | Tablas replicadas desde SQL Server |
| `VtaSch_*` | Schema ventas con prefijo explicito |
| `Rnt*` | Rentabilidad |
| `Amp*` | Modulo de precios (AMP) |

## Patrones de Consulta Comunes

### Precio efectivo de un articulo en una lista
```sql
SELECT
    d.ClaArticulo,
    a.NomArticulo,
    d.PrecioLista,
    d.PrecioUnitario,
    d.FecIni,
    d.FecFin
FROM "MSWTraListaPrecioDetDEAUSA" d
JOIN dbo_ArtCatArticulo a ON d.ClaArticulo = a.ClaArticulo
WHERE d.ClaListaPrecio = [ID_LISTA]
  AND d.ClaArticulo = [ID_ARTICULO]
  AND (d.FecFin IS NULL OR d.FecFin >= CURRENT_DATE)
ORDER BY d.FecIni DESC
LIMIT 1;
```

### Descuentos aplicados a un cliente
```sql
SELECT
    desc.ClaCliente,
    desc.ClaCuenta,
    desc.TipDescuento,
    desc.PjeDescuento,
    desc.FecIni,
    desc.FecFin
FROM "VtaTraListaDescuento" desc
WHERE desc.ClaCliente = [ID_CLIENTE]
  AND (desc.FecFin IS NULL OR desc.FecFin >= CURRENT_DATE)
ORDER BY desc.TipDescuento;
```

## Formato de Respuesta

```
## Resultado: [descripcion de la query]

### Query Ejecutada
```sql
[query]
```

### Resultados
| col1 | col2 | col3 |
|------|------|------|
| ... | ... | ... |

**Total registros**: N
**Observaciones**: [anomalias, valores NULL, rangos fuera de lo esperado]
```

## Degradacion

Si AlloyDB no esta disponible:
1. Verificar tunel: `lsof -i :5435`
2. Re-autenticar GCP: `gcloud auth login`
3. Reiniciar tunel: `./scripts/start-ssh-tunnel.sh`
4. Para datos legacy, usar `/sp-search` para consultar SQL Server via Squit
