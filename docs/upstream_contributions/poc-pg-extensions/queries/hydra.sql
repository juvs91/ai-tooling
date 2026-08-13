-- Experimento 2: Hydra Columnar. Correr en pg-hydra (localhost:5462) DESPUÉS de correr
-- seed_relational.sql también contra este contenedor (Hydra es un contenedor aparte, no
-- comparte datos con pg-combo).

-- Si "USING columnar" falla, correr primero: SELECT amname FROM pg_am; y usar el nombre
-- real del table access method de Hydra en su lugar.
CREATE TABLE poc.cade_columnar (LIKE poc.cross_all_discounts_with_entities) USING columnar;
CREATE TABLE poc.cade_rowstore (LIKE poc.cross_all_discounts_with_entities);

INSERT INTO poc.cade_columnar SELECT * FROM poc.cross_all_discounts_with_entities;
INSERT INTO poc.cade_rowstore SELECT * FROM poc.cross_all_discounts_with_entities;

SELECT pg_size_pretty(pg_total_relation_size('poc.cade_columnar')) AS tam_columnar,
       pg_size_pretty(pg_total_relation_size('poc.cade_rowstore')) AS tam_rowstore;

EXPLAIN ANALYZE
SELECT zone_id, count(*), avg((charges_and_discounts_json->'charges'->0->>'value')::numeric)
FROM poc.cade_columnar GROUP BY zone_id;

EXPLAIN ANALYZE
SELECT zone_id, count(*), avg((charges_and_discounts_json->'charges'->0->>'value')::numeric)
FROM poc.cade_rowstore GROUP BY zone_id;

-- Fricción real de UPDATE — documentación de Hydra dice "no recomendado para updates
-- frecuentes"; este UPDATE es para ver si directamente falla o solo es más costoso.
UPDATE poc.cade_columnar SET zone_id = 99 WHERE id = 1;

-- Comparar contra el mismo experimento en queries/citus.sql: ¿mismo comportamiento de
-- UPDATE, o Hydra sí lo rechaza donde Citus lo permite (reescribiendo el stripe)?
