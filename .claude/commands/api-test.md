# Prueba de Integracion con API de Arquitectura de Precios

Pruebas de integracion con la API de Arquitectura de Precios en GCP.

Parametros: $ARGUMENTS

Extrae de los argumentos: cliente (ClaClienteCuenta), consignado (ClaConsignado), articulo (ClaArticulo)

## Endpoints Disponibles

| Ambiente | URL |
|----------|-----|
| Desarrollo | https://prices-api-dev.run.app |
| Produccion | https://prices-api.run.app |

## Flujo de Prueba

### 1. Verificar que el consignado esta en tabla de control

```sql
SELECT
    ClaClienteCuenta,
    ClaConsignado,
    FechaAlta,
    BajaLogica
FROM "VtaSch_ArqPreciosConsignados"
WHERE ClaConsignado = [CONSIGNADO]
  AND BajaLogica = 0;
```

**Si NO existe**: El consignado usa sistema legacy (SQL Server), NO la API de Arquitectura de Precios. No procedera la integracion.

### 2. Verificar datos del cliente

```sql
SELECT
    ClaClienteCuenta,
    NomCliente,
    ClaNivelCanal,
    ClaEstatusCliente
FROM dbo_VtaCatClienteUnico
WHERE ClaClienteCuenta = [CLIENTE];
```

### 3. Verificar articulo existe y esta activo

```sql
SELECT
    ClaArticulo,
    ClaveArticulo,
    NomArticulo,
    EsObsoleto,
    EsDisponibleUso
FROM dbo_ArtCatArticulo
WHERE ClaArticulo = [ARTICULO]
  AND EsObsoleto = false
  AND EsDisponibleUso = true;
```

### 4. Simular llamada a API

El endpoint de integracion es:
```
GET /wpcprices/articles/integrations/comercial
```

Parametros:
- ClaCuenta: [CLIENTE]
- ClaConsignado: [CONSIGNADO]
- ClaArticulo: [ARTICULO]

Response esperada:
```json
{
  "ClaClienteCuenta": "[CLIENTE]",
  "ClaConsignado": "[CONSIGNADO]",
  "ClaArticulo": "[ARTICULO]",
  "Precio": "1187.21",
  "Moneda": "MXN",
  "FechaCalculo": "[timestamp]"
}
```

## Configuracion de Integracion

La configuracion se almacena en VtaCfgConfiguracion:

| ID | Parametro |
|----|-----------|
| 10204 | URL de API ARP |
| 10205 | URL de Identity Server |
| 10206 | Credentials OAuth |

## Validaciones de Integracion (7 bloqueantes)

| ID | Validacion | Descripcion |
|----|------------|-------------|
| V-API.1 | ClaCuenta | Debe ser entero positivo |
| V-API.2 | ClaConsignado | Debe ser entero positivo |
| V-API.3 | ClaArticulo | No puede estar vacio |
| V-API.4 | Authorization | Token Bearer valido |
| V-API.5 | Token expiry | Token no expirado |
| V-API.6 | Consignado autorizado | Existe en ArqPreciosConsignados |
| V-API.7 | Articulo existe | ClaArticulo valido y activo |

## Errores Comunes

| Codigo | Causa | Solucion |
|--------|-------|----------|
| 401 | Token OAuth expirado | Regenerar token via sp_ConsumeApiOAuth |
| 404 | Consignado no autorizado | Agregar a ArqPreciosConsignados |
| 404 | Articulo sin precio | Configurar precio en lista de precios |
| 500 | Error interno | Revisar logs en BigQuery |

## Bug Conocido

**ARP-495**: Integracion Arquitectura de Precio con Sistema Comercial no esta funcionando.

- **Estado**: QA Environment
- **Prioridad**: MUY ALTA
- **Sintoma**: El precio calculado por ARP no se refleja en Sistema Comercial
- **Causa probable**: sp_ConsumeApiOAuth no parsea correctamente la respuesta JSON

Revisar con `/jira-context ARP-495` para mas detalles.

## Formato de Respuesta

```
## Prueba de Integracion API

### Datos de Entrada
| Parametro | Valor |
|-----------|-------|
| Cliente | [CLIENTE] |
| Consignado | [CONSIGNADO] |
| Articulo | [ARTICULO] |

### Validaciones Previas
| Validacion | Estado | Detalle |
|------------|--------|---------|
| Consignado en piloto | OK/FAIL | [existe/no existe en ArqPreciosConsignados] |
| Cliente activo | OK/FAIL | [estado] |
| Articulo activo | OK/FAIL | [obsoleto/disponible] |

### Resultado Esperado
Si todas las validaciones pasan, la API deberia retornar:
- Precio: $[calculado]
- Moneda: MXN/USD
- Timestamp: [fecha]

### Problemas Detectados
[Lista de problemas encontrados]

### Recomendaciones
1. [Accion 1]
2. [Accion 2]
```

## Degradacion

Si AlloyDB no disponible:
1. Mostrar estructura de la API desde documentacion
2. Referir a `docs/arquitectura-precios/04-integracion/api-endpoints.md`
3. Indicar que se requiere conexion a BD para validacion completa
