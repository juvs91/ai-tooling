# Diagnostico de Errores de Calculo en AlloyDB

Diagnosticas discrepancias o errores en calculos de precios, descuentos y fletes usando AlloyDB (base `ods`).

Parametros: $ARGUMENTS — describir el error o ingresar IdFabricacion/ClaListaPrecio

## Prerequisitos

Tunel SSH activo en puerto 5435:
```bash
lsof -i :5435
```

## Metodologia de Debug

### Paso 1 — Identificar el contexto
Determinar del problema:
- Es un precio incorrecto? → Verificar lista de precios + descuentos
- Es un flete incorrecto? → Verificar asignacion de flete (cascada)
- Es un descuento que no aplica? → Verificar vigencia + cliente + tipo
- Es un calculo de rentabilidad? → Verificar participacion + pariedad

### Paso 2 — Localizar el registro

```sql
-- Por fabricacion
SELECT
    f.IdFabricacion,
    f.ClaAnioMes,
    f.ClaEscenario,
    f.ClaTipoEmbarque,
    f.TonsVenta,
    f.FecFabricacion
FROM "VtaSch_VtaTraFabricacion" f
WHERE f.IdFabricacion = [ID]
LIMIT 1;
```

```sql
-- Por lista de precios
SELECT
    l.ClaListaPrecio,
    l.NomListaPrecio,
    l.FecIni,
    l.FecFin,
    l.ClaMoneda
FROM "VtaTraListaPrecio" l
WHERE l.ClaListaPrecio = [ID]
   OR l.NomListaPrecio ILIKE '%[BUSQUEDA]%'
LIMIT 5;
```

### Paso 3 — Diagnostico por tipo de error

#### Error: Precio de lista incorrecto
```sql
-- Precio efectivo vs esperado
SELECT
    d.ClaArticulo,
    a.NomArticulo,
    d.PrecioLista,
    d.PrecioUnitario,
    d.FecIni,
    d.FecFin,
    CASE
        WHEN d.FecFin IS NULL THEN 'VIGENTE'
        WHEN d.FecFin < CURRENT_DATE THEN 'VENCIDO'
        ELSE 'VIGENTE'
    END as Estado
FROM "MSWTraListaPrecioDetDEAUSA" d
JOIN dbo_ArtCatArticulo a ON d.ClaArticulo = a.ClaArticulo
WHERE d.ClaListaPrecio = [ID_LISTA]
  AND d.ClaArticulo = [ID_ARTICULO]
ORDER BY d.FecIni DESC;
```

#### Error: Descuento no aplicado o incorrecto
```sql
-- Todos los descuentos activos para un cliente
SELECT
    d.TipDescuento,
    d.ClaCliente,
    d.ClaCuenta,
    d.PjeDescuento,
    d.FecIni,
    d.FecFin,
    CASE
        WHEN d.FecFin IS NULL OR d.FecFin >= CURRENT_DATE THEN 'ACTIVO'
        ELSE 'EXPIRADO'
    END as Estado
FROM "VtaTraListaDescuento" d
WHERE d.ClaCliente = [ID_CLIENTE]
ORDER BY d.TipDescuento, d.FecIni DESC;

-- Comparar descuento esperado vs aplicado en fabricacion
SELECT
    fd.NumeroRenglon,
    fd.ClaArticulo,
    fd.PrecioLista,
    fd.PrecioUnitario,
    fd.DescuentoFuncional,
    fd.DescuentoVolumen,
    fd.DescuentoFlotante,
    fd.DescuentoPP,
    (fd.DescuentoFuncional + fd.DescuentoVolumen + fd.DescuentoFlotante + fd.DescuentoPP) as TotalDescuento,
    ROUND(
        (fd.DescuentoFuncional + fd.DescuentoVolumen + fd.DescuentoFlotante + fd.DescuentoPP)
        / NULLIF(fd.PrecioLista, 0) * 100, 2
    ) as PjeDescuentoAplicado
FROM "VtaSch_VtaTraFabricacionDet" fd
WHERE fd.IdFabricacion = [ID_FABRICACION]
ORDER BY fd.NumeroRenglon;
```

#### Error: Flete incorrecto
```sql
-- Asignacion de flete para una fabricacion
SELECT
    af.IdFabricacion,
    af.PriceFleteDistribucion,
    af.PriceFleteDistribucionUSA,
    af.PriceAlmacenaje,
    af.PriceAlmacenajeUSA,
    af.PriceManejoUSA,
    af.PriceOpPuertoOri,
    af.PriceFleteEntrega,
    af.ParidadImporte,
    af.PjeParticipacion,
    af.TonsVenta
FROM "RntTraAsignacionFlete" af
WHERE af.IdFabricacion = [ID_FABRICACION];
```

#### Error: Pariedad / tipo de cambio
```sql
-- Paridades registradas en el periodo
SELECT
    p.ClaAnioMes,
    p.ClaMoneda,
    p.Pariedad,
    p.FecRegistro
FROM "FinTraPariedad" p
WHERE p.ClaAnioMes = [YYYYMM]
ORDER BY p.ClaMoneda;
```

### Paso 4 — Comparacion de totales

```sql
-- Cuadre de una fabricacion: precio lista vs precio final vs flete
SELECT
    f.IdFabricacion,
    f.ClaAnioMes,
    SUM(d.PrecioLista * d.Cantidad) as TotalPrecioLista,
    SUM(d.PrecioUnitario * d.Cantidad) as TotalPrecioUnitario,
    SUM(d.ImporteCargoFlete) as TotalFlete,
    SUM(d.ImporteCargoFleteZona) as TotalFleteZona,
    SUM(d.DescuentoFuncional + d.DescuentoVolumen + d.DescuentoFlotante + d.DescuentoPP) as TotalDescuentos,
    SUM(d.PrecioUnitario * d.Cantidad + d.ImporteCargoFlete) as TotalFacturado
FROM "VtaSch_VtaTraFabricacion" f
JOIN "VtaSch_VtaTraFabricacionDet" d ON f.IdFabricacion = d.IdFabricacion
WHERE f.IdFabricacion = [ID_FABRICACION]
GROUP BY f.IdFabricacion, f.ClaAnioMes;
```

## Errores Comunes y Causas

| Sintoma | Causa Probable | Query de Verificacion |
|---------|----------------|----------------------|
| Precio mas alto de lo esperado | Lista vencida activa / lista equivocada | Verificar FecFin en lista |
| Descuento no aparece | FecFin expirada / cliente sin descuento | Query descuentos activos |
| Descuento funcional 0 | Sin descuento funcional asignado | Filtrar TipDescuento = 'FUN' |
| Flete cero en USA | ClaTipoEmbarque != 6 o 7 | Verificar tipo en fabricacion |
| Pariedad erronea | Pariedad del mes diferente | Query paridades del periodo |
| Total no cuadra | Redondeos o renglon sin datos | Revisar por NumeroRenglon |

## Formato de Respuesta

```
## Diagnostico: [descripcion del error]

### Contexto
- Fabricacion: [ID] / Lista: [ID]
- Periodo: [YYYYMM]
- Tipo de error: [precio / descuento / flete / pariedad]

### Hallazgos

| Concepto | Valor Esperado | Valor Encontrado | Delta |
|----------|----------------|------------------|-------|
| PrecioLista | $XXX | $YYY | $ZZZ |
| DescuentoFuncional | XX% | YY% | ZZ% |
| TotalFlete | $XXX | $YYY | $ZZZ |

### Causa Raiz
[Descripcion de la discrepancia encontrada]

### Queries Ejecutadas
```sql
[queries relevantes]
```

### Recomendacion
[Siguiente paso: corrección de datos / consulta a SQL Server legacy / escalacion]
```

## Degradacion

Si las tablas no existen en AlloyDB o el dato no esta disponible:
1. Datos de cascada → usar `/cascade-analyzer` para analisis de flete USA/Canada
2. SP de calculo → usar `/sp-search` para buscar logica en SQL Server (Squit)
3. Datos historicos → pueden estar solo en RentabilidadRDS (SQL Server legacy)
