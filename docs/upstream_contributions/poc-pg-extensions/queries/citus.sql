-- Experimento 1: Citus columnar. Correr en pg-combo (localhost:5461) DESPUÉS de
-- seed_relational.sql.

-- Tabla columnar y su gemela row-store, mismos datos
CREATE TABLE poc.cade_columnar (LIKE poc.cross_all_discounts_with_entities) USING columnar;
CREATE TABLE poc.cade_rowstore (LIKE poc.cross_all_discounts_with_entities);

INSERT INTO poc.cade_columnar SELECT * FROM poc.cross_all_discounts_with_entities;
INSERT INTO poc.cade_rowstore SELECT * FROM poc.cross_all_discounts_with_entities;

-- Observar: tamaño en disco (columnar debería comprimir) y el plan de la agregación
SELECT pg_size_pretty(pg_total_relation_size('poc.cade_columnar')) AS tam_columnar,
       pg_size_pretty(pg_total_relation_size('poc.cade_rowstore')) AS tam_rowstore;

EXPLAIN ANALYZE
SELECT zone_id, count(*), avg((charges_and_discounts_json->'charges'->0->>'value')::numeric)
FROM poc.cade_columnar GROUP BY zone_id;

EXPLAIN ANALYZE
SELECT zone_id, count(*), avg((charges_and_discounts_json->'charges'->0->>'value')::numeric)
FROM poc.cade_rowstore GROUP BY zone_id;

-- Observar: fricción real de UPDATE en columnar (la razón de fondo por la que columnar
-- clásico no encaja con el patrón UPSERT del pipeline de precios real)
UPDATE poc.cade_columnar SET zone_id = 99 WHERE id = 1;
-- Si esto corre sin error, comparar el costo real (EXPLAIN ANALYZE) contra el mismo UPDATE
-- en la tabla row-store — Citus columnar SÍ soporta UPDATE/DELETE (a diferencia de
-- cstore_fdw), pero reescribe el stripe completo por debajo.
EXPLAIN ANALYZE UPDATE poc.cade_columnar SET zone_id = 98 WHERE id = 2;
EXPLAIN ANALYZE UPDATE poc.cade_rowstore SET zone_id = 98 WHERE id = 2;
