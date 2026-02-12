# Verificacion de Salud del Entorno

Verificas el estado de todos los servicios necesarios para trabajar en Arquitectura de Precios.

## Checklist de Servicios

### 1. Tunel SSH a AlloyDB (Puerto 5435)

Ejecutar:
```bash
lsof -i :5435
```

**Estado esperado**: Proceso ssh escuchando en el puerto

Si NO esta activo, mostrar comando para iniciar:
```bash
./scripts/start-ssh-tunnel.sh
```

O manualmente:
```bash
gcloud compute ssh wpc-alloydb-proxy \
    --project dea-ods-prj-prod \
    --zone us-central1-a \
    -- -NL 5435:localhost:5432
```

### 2. Servidor Squit (Puerto 8000) - OPCIONAL

Ejecutar:
```bash
curl -s http://localhost:8000/mcp 2>/dev/null | head -c 100
```

**Estado esperado**: Respuesta (aunque sea error JSON-RPC)

Si no responde: Squit es opcional, los skills funcionan con documentacion local.

### 3. Autenticacion GCP

Ejecutar:
```bash
gcloud auth print-access-token 2>/dev/null | head -c 20
```

**Estado esperado**: Token (primeros 20 caracteres visibles)

Si falla:
```bash
gcloud auth login
```

### 4. Conexion AlloyDB via MCP

Si el tunel esta activo, intentar query simple usando el MCP de AlloyDB:

```sql
SELECT 1 as test, current_database() as db, version() as version;
```

### 5. Atlassian MCP

Siempre disponible (Cloud). No requiere verificacion local.

### 6. Bitbucket MCP

Siempre disponible (Cloud). No requiere verificacion local.

## Tabla de Estado

Genera una tabla con el estado de cada servicio:

| Servicio | Puerto/Metodo | Estado | Requerido | Accion |
|----------|---------------|--------|-----------|--------|
| SSH Tunnel | lsof :5435 | [OK/FAIL] | SI | ./scripts/start-ssh-tunnel.sh |
| Squit | curl :8000 | [OK/WARN] | NO | Opcional |
| GCP Auth | gcloud auth | [OK/FAIL] | SI | gcloud auth login |
| AlloyDB MCP | Query test | [OK/FAIL] | SI | Verificar tunel |
| Atlassian | Cloud | OK | NO | Siempre disponible |
| Bitbucket | Cloud | OK | NO | Siempre disponible |

## Iconos de Estado

- OK: Servicio funcionando correctamente
- WARN: Servicio no disponible pero opcional
- FAIL: Servicio requerido no disponible

## Permisos GCP Requeridos

Si el tunel SSH falla, verificar que el usuario tenga:
- `roles/iam.serviceAccountUser`
- `roles/iap.tunnelResourceAccessor`
- `roles/compute.osLogin`

## Acciones de Recuperacion

### Tunel SSH caido
1. Verificar autenticacion: `gcloud auth login`
2. Verificar permisos IAP
3. Reiniciar tunel: `./scripts/start-ssh-tunnel.sh`

### AlloyDB no responde
1. Verificar que el tunel este activo
2. Verificar credenciales en `.env`
3. Probar conexion: `./scripts/test-alloydb-connection.sh`

### Squit no responde
- Este servicio es opcional
- Los skills usan `docs/arquitectura-precios/03-sql-legacy/` como alternativa
- No bloquea el trabajo

## Formato de Respuesta

```
## Estado del Entorno - Arquitectura de Precios

### Resumen
[X de Y servicios activos]

### Detalle de Servicios

| Servicio | Estado | Detalle |
|----------|--------|---------|
| SSH Tunnel (5435) | [OK/FAIL] | [PID o "No activo"] |
| Squit (8000) | [OK/WARN] | [Responde / No disponible] |
| GCP Auth | [OK/FAIL] | [Autenticado / No autenticado] |
| AlloyDB MCP | [OK/FAIL] | [Conectado / Error] |
| Atlassian | OK | Cloud - siempre disponible |
| Bitbucket | OK | Cloud - siempre disponible |

### Acciones Requeridas
[Lista de comandos a ejecutar si hay servicios caidos]

### Skills Disponibles
Con el estado actual, puedes usar:
- [lista de skills que funcionan]
```

## Script Auxiliar

Tambien puedes ejecutar el script de verificacion:
```bash
./scripts/check-mcp-status.sh
```
