# Validacion de Reglas de Negocio

Validas reglas de negocio del dominio de Arquitectura de Precios: listas vigentes, descuentos consistentes, configuraciones de integracion y datos maestros.

Parametros: $ARGUMENTS — area a validar (precios, descuentos, clientes, articulos, integracion)

## Areas de Validacion

### 1. Listas de Precios

```sql
-- Listas activas (sin FecFin o FecFin futura)
SELECT
    ClaListaPrecio,
    NomListaPrecio,
    FecIni,
    FecFin,
    ClaMoneda,
    CASE WHEN FecFin IS NULL OR FecFin >= CURRENT_DATE THEN 'VIGENTE' ELSE 'VENCIDA' END as Estado
FROM "VtaTraListaPrecio"
ORDER BY FecIni DESC
LIMIT 50;

-- Listas con solapamiento de fechas (anomalia)
SELECT a.ClaListaPrecio, a.NomListaPrecio, a.FecIni, a.FecFin,
       b.ClaListaPrecio as OtraLista, b.FecIni as OtraFecIni, b.FecFin as OtraFecFin
FROM "VtaTraListaPrecio" a
JOIN "VtaTraListaPrecio" b ON a.ClaListaPrecio <> b.ClaListaPrecio
  AND a.FecIni < COALESCE(b.FecFin, '9999-12-31')
  AND b.FecIni < COALESCE(a.FecFin, '9999-12-31')
WHERE (a.FecFin IS NULL OR a.FecFin >= CURRENT_DATE)
  AND (b.FecFin IS NULL OR b.FecFin >= CURRENT_DATE)
LIMIT 20;

-- Articulos sin precio en lista activa
SELECT a.ClaArticulo, a.NomArticulo
FROM dbo_ArtCatArticulo a
WHERE a.EsObsoleto = false
  AND a.EsDisponibleUso = true
  AND NOT EXISTS (
    SELECT 1 FROM "MSWTraListaPrecioDetDEAUSA" d
    JOIN "VtaTraListaPrecio" l ON d.ClaListaPrecio = l.ClaListaPrecio
    WHERE d.ClaArticulo = a.ClaArticulo
      AND (l.FecFin IS NULL OR l.FecFin >= CURRENT_DATE)
  )
LIMIT 50;
```

### 2. Descuentos

```sql
-- Descuentos vencidos aun activos (datos sucios)
SELECT ClaCliente, ClaCuenta, TipDescuento, PjeDescuento, FecFin
FROM "VtaTraListaDescuento"
WHERE FecFin < CURRENT_DATE
ORDER BY FecFin DESC
LIMIT 20;

-- Clientes con descuento funcional > 30% (anomalia potencial)
SELECT ClaCliente, ClaCuenta, PjeDescuento, TipDescuento, FecIni, FecFin
FROM "VtaTraListaDescuento"
WHERE TipDescuento = 'FUN'
  AND PjeDescuento > 0.30
  AND (FecFin IS NULL OR FecFin >= CURRENT_DATE)
ORDER BY PjeDescuento DESC;

-- Clientes con multiples descuentos funcionales activos (conflicto)
SELECT ClaCliente, COUNT(*) as NumDescuentos
FROM "VtaTraListaDescuento"
WHERE TipDescuento = 'FUN'
  AND (FecFin IS NULL OR FecFin >= CURRENT_DATE)
GROUP BY ClaCliente
HAVING COUNT(*) > 1
ORDER BY NumDescuentos DESC;
```

### 3. Integracion API (piloto)

```sql
-- Consignados en piloto de Arquitectura de Precios
SELECT
    ClaClienteCuenta,
    ClaConsignado,
    FechaAlta,
    BajaLogica
FROM "VtaSch_ArqPreciosConsignados"
WHERE BajaLogica = 0
ORDER BY FechaAlta DESC;

-- Configuracion de la integracion
SELECT ID, Valor, Descripcion
FROM "VtaCfgConfiguracion"
WHERE ID IN (10204, 10205, 10206)
ORDER BY ID;
```

### 4. Articulos Maestros

```sql
-- Articulos obsoletos con precio activo (inconsistencia)
SELECT a.ClaArticulo, a.NomArticulo, a.EsObsoleto, d.ClaListaPrecio, d.PrecioLista
FROM dbo_ArtCatArticulo a
JOIN "MSWTraListaPrecioDetDEAUSA" d ON a.ClaArticulo = d.ClaArticulo
JOIN "VtaTraListaPrecio" l ON d.ClaListaPrecio = l.ClaListaPrecio
WHERE a.EsObsoleto = true
  AND (l.FecFin IS NULL OR l.FecFin >= CURRENT_DATE)
LIMIT 30;

-- Articulos sin peso teorico (bloquea calculo de flete)
SELECT ClaArticulo, NomArticulo, PesoTeoricoKgs
FROM dbo_ArtCatArticulo
WHERE (PesoTeoricoKgs IS NULL OR PesoTeoricoKgs = 0)
  AND EsObsoleto = false
  AND EsDisponibleUso = true
LIMIT 30;
```

### 5. Consistencia de Fabricaciones

```sql
-- Fabricaciones sin detalle (renglones 0)
SELECT f.IdFabricacion, f.ClaAnioMes, f.FecFabricacion, f.TonsVenta
FROM "VtaSch_VtaTraFabricacion" f
WHERE NOT EXISTS (
    SELECT 1 FROM "VtaSch_VtaTraFabricacionDet" d
    WHERE d.IdFabricacion = f.IdFabricacion
)
AND f.ClaAnioMes >= (EXTRACT(YEAR FROM CURRENT_DATE) * 100 + EXTRACT(MONTH FROM CURRENT_DATE) - 3)
LIMIT 20;

-- Fabricaciones con precio unitario cero (anomalia)
SELECT fd.IdFabricacion, fd.NumeroRenglon, fd.ClaArticulo, fd.PrecioUnitario, fd.PrecioLista
FROM "VtaSch_VtaTraFabricacionDet" fd
JOIN "VtaSch_VtaTraFabricacion" f ON fd.IdFabricacion = f.IdFabricacion
WHERE fd.PrecioUnitario = 0
  AND f.ClaAnioMes >= (EXTRACT(YEAR FROM CURRENT_DATE) * 100 + EXTRACT(MONTH FROM CURRENT_DATE) - 1)
LIMIT 30;
```

## Reglas de Negocio Criticas

| Regla | Descripcion | Severidad |
|-------|-------------|-----------|
| R-01 | Todo articulo activo debe tener precio en lista vigente | CRITICA |
| R-02 | No pueden haber dos listas del mismo tipo con fechas solapadas | ALTA |
| R-03 | Descuento funcional max 30% (por politica) | ALTA |
| R-04 | Articulos obsoletos no deben tener precio activo | MEDIA |
| R-05 | Articulos activos deben tener PesoTeoricoKgs > 0 | ALTA |
| R-06 | Fabricaciones deben tener al menos 1 renglon | ALTA |
| R-07 | PrecioUnitario nunca puede ser 0 en fabricacion | CRITICA |
| R-08 | Consignados en piloto deben tener BajaLogica = 0 | ALTA |

## Formato de Respuesta

```
## Validacion de Reglas de Negocio: [area]

### Resumen Ejecutivo
| Regla | Estado | Registros Afectados |
|-------|--------|---------------------|
| R-01  | OK / FALLO | N |
| R-02  | OK / FALLO | N |
| ...   | ...   | ... |

### Anomalias Detectadas
#### [Regla que falla]
| Campo | Valor | Detalle |
|-------|-------|---------|
| ... | ... | ... |

**Impacto**: [descripcion del impacto en negocio]
**Accion recomendada**: [correccion sugerida]

### Estado General
[VERDE: sin anomalias / AMARILLO: anomalias menores / ROJO: anomalias criticas]
```

## Degradacion

Si AlloyDB no esta disponible:
1. Listar reglas de negocio conocidas desde esta documentacion
2. Indicar que la validacion requiere conexion a AlloyDB
3. Para validacion de integracion API: `/api-test` con los parametros del consignado
