-- Corre una sola vez, al crear el volumen del contenedor (docker-entrypoint-initdb.d).
CREATE EXTENSION IF NOT EXISTS citus;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';

-- Para que DBeaver/psql no tengan que fijar el search_path en cada sesión nueva.
ALTER DATABASE poc SET search_path = ag_catalog, "$user", public;
