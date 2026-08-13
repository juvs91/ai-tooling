-- Experimento 4 (combinado): ¿se complementan AGE (topología) y Citus columnar (agregación
-- de hechos)? Correr en pg-combo (localhost:5461) DESPUÉS de citus.sql, age.sql y
-- seed_graph.sql (necesita las 3 cosas: grafo cargado + tabla columnar poblada).
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- AGE resuelve "quién hereda de quién" (topología) → esa lista de reference_id filtra y
-- agrega sobre la tabla columnar de Citus (hechos). agtype se castea a int para poder
-- usarlo en un JOIN normal con SQL.
WITH descendientes AS (
    SELECT (ref_id)::text::int AS reference_id
    FROM cypher('precios_graph', $$
        MATCH (raiz:Articulo {ref_id: 1, level: 1})<-[:CHILD_OF*1..3]-(d)
        RETURN d.ref_id
    $$) AS (ref_id agtype)
)
SELECT c.zone_id,
       count(*) AS filas,
       avg((c.charges_and_discounts_json->'charges'->0->>'value')::numeric) AS avg_flete
FROM poc.cade_columnar c
JOIN descendientes d ON d.reference_id = c.reference_id
GROUP BY c.zone_id;

-- Qué observar:
--   1. ¿Corrió sin fricción, o hubo que castear/convertir agtype en varios puntos?
--   2. EXPLAIN ANALYZE de esta consulta — ¿el planner integra bien el resultado de cypher()
--      (que es una función, no una tabla real) con el escaneo columnar, o se ve un nested
--      loop costoso por fila?
--   3. Comparar el tiempo total contra hacer el mismo filtro con el WITH RECURSIVE de
--      queries/age.sql en vez de cypher() — ¿de verdad AGE aporta algo aquí, o el CTE
--      recursivo ya resolvía esto igual de bien sin la complejidad de mezclar dos motores?
EXPLAIN ANALYZE
WITH descendientes AS (
    SELECT (ref_id)::text::int AS reference_id
    FROM cypher('precios_graph', $$
        MATCH (raiz:Articulo {ref_id: 1, level: 1})<-[:CHILD_OF*1..3]-(d)
        RETURN d.ref_id
    $$) AS (ref_id agtype)
)
SELECT c.zone_id, count(*)
FROM poc.cade_columnar c
JOIN descendientes d ON d.reference_id = c.reference_id
GROUP BY c.zone_id;
