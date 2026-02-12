# Busqueda de Stored Procedures

Usas Squit para busqueda semantica en 5.7M objetos SQL del sistema legacy de DeAcero.

Termino de busqueda: $ARGUMENTS

## Verificacion de Prerequisitos

Squit requiere servidor en localhost:8000. Verificar:
```bash
curl -s http://localhost:8000/mcp 2>/dev/null | head -c 100
```

## Busqueda con Squit MCP

### 1. Busqueda semantica
Usar `mcp__squit__squit_search` con:
- query: "$ARGUMENTS"
- limit: 20

### 2. Obtener codigo completo
Para cada resultado relevante, usar `mcp__squit__squit_get_code` con el nombre del objeto.

### 3. Obtener dependencias
Usar `mcp__squit__squit_dependencies` para ver que tablas/objetos usa el SP.

### 4. Analisis de impacto
Usar `mcp__squit__squit_impact` para ver que objetos se afectarian si se modifica.

## Procedimientos Conocidos por Categoria

### Gestion de Listas de Precios (MCSW_ERP)
| Procedimiento | Criticidad | Funcion |
|---------------|------------|---------|
| MSW_CU212_Pag101_Grid_PriceListDet_Sel | CRITICA | Consulta detalles de lista |
| MSW_CU212_Pag101_Boton_Save_Proc | CRITICA | Guarda precios + autoriza |
| MSW_CU211_Pag2_Grid_PriceList_SelDO | CRITICA | Seleccion de listas |
| AmpAplicaCambioPrecioListaProc | ALTA | Aplica cambios autorizados |

### Cotizador CU221 (Ventas)
| Procedimiento | Criticidad | Funcion |
|---------------|------------|---------|
| VTA_CU221_Pag1_Boton_ClaClienteCuenta_Proc | MAXIMA | Valida cliente, carga descuentos |
| VTA_CU221_Pag1_Grid_GridCotiza_Sel | MAXIMA | Busca articulos |
| VTA_CU221_Pag50_Boton_SAVE_Proc | MAXIMA | Valida dumping |
| Vta_CU221_Pag50_Boton_BotonAceptar_Proc | ALTA | Crea orden fabricacion |
| Vta_CU221_Pag50_Boton_ConsultarFlete_Proc | MEDIA | Calcula flete |

### Calculo de Precios (VtaSch)
| Procedimiento | Criticidad | Funcion |
|---------------|------------|---------|
| VtaCalculaPreNetFactProc | CRITICA | Calcula precio neto final |
| VtaObtenerDescuentoLineaProc | ALTA | Descuento por linea |
| VtaObtenerDescuentoConfProc | ALTA | Descuento por configuracion |
| VtaObtenerDescuentoPPProc | MEDIA | Descuento pronto pago |

### Rentabilidad y Cascada (RentabilidadRDS)
| Procedimiento | Funcion |
|---------------|---------|
| RNT_CU20_Pag100_ConstruyeCascada_PriceProformaEUA_Prc | Cascada flete USA |
| RNT_CU20_Pag100_ConstruyeCascada_PriceAsignacionFlete_Prc | Asignacion de fletes |

### Integracion API
| Procedimiento | Funcion |
|---------------|---------|
| sp_ConsumeApiOAuth | Llamada a API de Arquitectura de Precios |

## Tablas Criticas Relacionadas

| Tabla | Base | Registros | Uso |
|-------|------|-----------|-----|
| MSWTraListaPrecioDetDEAUSA | MCSW_ERP | 245K | Detalle precios |
| VtaTraListaPrecio | Ventas | 1.5K | Encabezado listas |
| VtaTraListaDescuento | Ventas | 835 | Descuentos |
| ArtCatArticulo | Maestros | 404K | Catalogo articulos |
| VtaTraFabricacionDet | Ventas | 13.6M | Ordenes de venta |

## Formato de Respuesta

Para cada SP encontrado:

```
## [Nombre del SP]

**Base de datos**: [MCSW_ERP / Ventas / RentabilidadRDS]
**Criticidad**: [CRITICA / ALTA / MEDIA / BAJA]
**Complejidad**: [score si disponible]

### Proposito
[Descripcion de lo que hace]

### Parametros Principales
- @param1: [descripcion]
- @param2: [descripcion]

### Tablas que Usa
- [tabla1]
- [tabla2]

### Dependientes
[Numero de objetos que lo llaman]

### Codigo (fragmento)
```sql
[primeras 50 lineas relevantes]
```
```

## Degradacion Elegante

Si Squit no esta disponible:

1. Informar: "Servidor Squit no disponible en localhost:8000"
2. Buscar en documentacion local:
   - `docs/arquitectura-precios/03-sql-legacy/stored-procedures.md`
   - `docs/arquitectura-precios/03-sql-legacy/cotizador-cu221.md`
   - `docs/arquitectura-precios/03-sql-legacy/cascada-precios.md`
   - `docs/arquitectura-precios/03-sql-legacy/tablas-criticas.md`
3. Mostrar procedimientos conocidos de la categoria relevante
4. Indicar que la informacion puede no estar actualizada
