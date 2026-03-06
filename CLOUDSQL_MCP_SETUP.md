# CloudSQL MCP - Configuración

## Archivos

- `.cloudsql-env.template` - Template de configuración (sin credenciales)
- `scripts/cloudsql-mcp.sh` - Wrapper que llama al MCP
- `mcp-scripts/cloudsql-mcp.sh` - Copia del wrapper (copiada desde otro proyecto)

## Configuración

1. **Copiar el template:**
   ```bash
   cp .cloudsql-env.template .cloudsql-env
   ```

2. **Editar `.cloudsql-env`** con las credenciales de tu entorno:
   ```bash
   WPC_ENV=qa  # Cambia a dev/prod según necesites
   QA_USER=wpc-usr-prices
   QA_PASSWORD=YOUR_QA_PASSWORD
   QA_DATABASE=prices
   QA_PORT=5432
   ```

3. **Variables de entorno:**
   - `WPC_ENV`: Ambiente (dev/qa/prod)
   - `${WPC_ENV}_USER`: Usuario de base de datos
   - `${WPC_ENV}_PASSWORD`: Contraseña de base de datos
   - `${WPC_ENV}_DATABASE`: Nombre de base de datos
   - `${WPC_ENV}_PORT`: Puerto (5432 para Cloud SQL)

4. **Configuración en `.mcp.json`:**
   ```json
   "cloudsql": {
     "command": "bash",
     "args": ["./scripts/cloudsql-mcp.sh"]
   }
   ```

## Funcionamiento

El script `cloudsql-mcp.sh`:
1. Lee `.cloudsql-env` y selecciona el ambiente
2. URL-encode las credenciales
3. Construye la URL de conexión: `postgresql://user:password@localhost:5432/database`
4. Exporta `DB_MAIN_URL` como variable de entorno
5. Ejecuta `postgres-mcp` (node o npx)

## Troubleshooting

Si el MCP de cloudsql falla:

1. **Verificar que `.cloudsql-env` existe:**
   ```bash
   ls -la .cloudsql-env
   ```

2. **Verificar que las credenciales están configuradas:**
   ```bash
   source .cloudsql-env
   echo "Usuario: ${QA_USER}"
   echo "Database: ${QA_DATABASE}"
   echo "Puerto: ${QA_PORT}"
   ```

3. **Verificar que `postgres-mcp` está instalado:**
   ```bash
   npx postgres-mcp --version
   ```

4. **Prueba de conexión:**
   ```bash
   export DB_MAIN_URL="postgresql://user:password@localhost:5432/database"
   npx postgres-mcp
   ```
