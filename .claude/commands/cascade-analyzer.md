# Analisis de Cascada de Precios

Analizas la cascada de precios/flete para proformas USA/Canada (ClaTipoEmbarque 6 o 7).

Parametros: $ARGUMENTS

Extrae: IdFabricacion, ClaAnioMes (formato YYYYMM), ClaEscenario

## Documentacion de Referencia

Consultar: `docs/arquitectura-precios/03-sql-legacy/cascada-precios.md`

## Flujo de la Cascada de Precios

```
1. ENTRADA
   Facturas Proforma (RntTraProforma)
   Facturas Comerciales (RntTraFacturaComercial)
       |
       v
2. IDENTIFICACION
   IdFabricacion por cada factura
   Toneladas por fabricacion
   Participacion % = TonsFabrica / TonsTotal
       |
       v
3. TARIFAS DE FLETE
   RntTraAsignacionFlete
   - 7 conceptos de costo
   - Pariedad USD/MXN
       |
       v
4. CALCULO
   ImpPrice = PriceBase * ParidadImporte * Participacion * TonsVenta
       |
       v
5. RESULTADO
   RntTraProformaFleteUSAStd (historico)
```

## Consultas de Analisis

### 1. Datos de la fabricacion

```sql
SELECT
    fd.IdFabricacion,
    fd.NumeroRenglon,
    fd.ClaArticulo,
    fd.ClaListaPrecio,
    fd.PrecioLista,
    fd.PrecioUnitario,
    fd.PrecioEntrega,
    fd.DescuentoFuncional,
    fd.DescuentoVolumen,
    fd.DescuentoFlotante,
    fd.ImporteCargoFlete,
    fd.ImporteCargoFleteZona,
    a.NomArticulo,
    a.PesoTeoricoKgs
FROM "VtaSch_VtaTraFabricacionDet" fd
LEFT JOIN dbo_ArtCatArticulo a ON fd.ClaArticulo = a.ClaArticulo
WHERE fd.IdFabricacion = [FABRICACION]
ORDER BY fd.NumeroRenglon;
```

### 2. Totales de la fabricacion

```sql
SELECT
    IdFabricacion,
    COUNT(*) as NumRenglones,
    SUM(PrecioLista * Cantidad) as TotalPrecioLista,
    SUM(PrecioUnitario * Cantidad) as TotalPrecioUnitario,
    SUM(ImporteCargoFlete) as TotalFlete,
    SUM(ImporteCargoFleteZona) as TotalFleteZona
FROM "VtaSch_VtaTraFabricacionDet"
WHERE IdFabricacion = [FABRICACION]
GROUP BY IdFabricacion;
```

### 3. Descuentos aplicados

```sql
SELECT
    fd.IdFabricacion,
    SUM(fd.DescuentoFuncional) as TotalDescFuncional,
    SUM(fd.DescuentoVolumen) as TotalDescVolumen,
    SUM(fd.DescuentoFlotante) as TotalDescFlotante,
    SUM(fd.DescuentoPP) as TotalDescPP
FROM "VtaSch_VtaTraFabricacionDet" fd
WHERE fd.IdFabricacion = [FABRICACION]
GROUP BY fd.IdFabricacion;
```

## Conceptos de Flete (7 componentes)

| # | Concepto | Campo | Descripcion |
|---|----------|-------|-------------|
| 1 | Flete Distribucion MX | PriceFleteDistribucion | Costo distribucion Mexico |
| 2 | Flete Distribucion USA | PriceFleteDistribucionUSA | Costo distribucion USA |
| 3 | Almacenaje Base | PriceAlmacenaje | Almacenamiento base |
| 4 | Almacenaje USA | PriceAlmacenajeUSA | Almacenamiento en USA |
| 5 | Manejo USA | PriceManejoUSA | Costos de manejo |
| 6 | Operaciones Puerto | PriceOpPuertoOri | Puerto de origen |
| 7 | Flete Entrega | PriceFleteEntrega | Ultima milla |

## Formula de Calculo

Para cada concepto:
```
ImpPrice[concepto] = Price[concepto] * ParidadImporte * PjeParticipacion * TonsVenta
```

Donde:
- **ParidadImporte**: Tipo de cambio USD/MXN (~19.50-20.50)
- **PjeParticipacion**: % de participacion de la fabricacion en el total
- **TonsVenta**: Toneladas vendidas

## Tipos de Embarque

| Codigo | Destino |
|--------|---------|
| 6 | USA |
| 7 | Canada |

## Ejemplo de Calculo

```
Fabricacion: 78901
Toneladas: 125.50
Pariedad: 19.87 USD/MXN
Participacion: 100%

Flete Dist. USA: $15.00/ton
ImpPriceFleteDistUSA = 15.00 * 19.87 * 1.0 * 125.50 = $37,405.43 MXN

Total 7 conceptos: $113,463.13 MXN
Costo por tonelada: $904.09 MXN/ton
```

## Formato de Respuesta

```
## Cascada de Precios - Fabricacion [ID]

### Datos Generales
| Campo | Valor |
|-------|-------|
| IdFabricacion | [ID] |
| Mes/Periodo | [YYYYMM] |
| Renglones | [N] |
| Tipo Embarque | [6=USA / 7=Canada] |

### Articulos en la Fabricacion
| Renglon | Articulo | Precio Lista | Precio Unit | Cantidad |
|---------|----------|--------------|-------------|----------|
| 1 | [nombre] | $[precio] | $[unit] | [cant] |

### Descuentos Aplicados
| Tipo | Monto |
|------|-------|
| Funcional | $[valor] |
| Volumen | $[valor] |
| Flotante | $[valor] |
| Pronto Pago | $[valor] |
| **Total Descuentos** | **$[total]** |

### Cargos de Flete
| Concepto | Monto |
|----------|-------|
| Flete | $[valor] |
| Flete Zona | $[valor] |
| **Total Flete** | **$[total]** |

### Resumen
| Concepto | Valor |
|----------|-------|
| Total Precio Lista | $[valor] |
| - Descuentos | $[valor] |
| + Fletes | $[valor] |
| **Total Final** | **$[valor]** |
| Precio por Tonelada | $[valor]/ton |
```

## Degradacion

Si las tablas de rentabilidad no estan disponibles en AlloyDB:

1. Informar que las tablas de cascada estan en RentabilidadRDS (SQL Server legacy)
2. Mostrar formulas de calculo desde documentacion
3. Sugerir usar Squit: `/sp-search cascada flete`
4. Referir a: `docs/arquitectura-precios/03-sql-legacy/cascada-precios.md`
