-- DEEP DIVE: ¿reemplazar CADE precomputada por resolución "al vuelo" del ancestro más
-- cercano con config propia? Correr en pg-combo (5461) DESPUÉS de
-- deep_dive_age_resolve.sql (que marca ~25 nodos con "config propia" en el grafo).

-- Parte A: la MISMA regla, pero en el mundo relacional (para comparar manzanas con
-- manzanas contra lo que hoy hace propagation_tree.config_source_chain)
DROP TABLE IF EXISTS poc.article_own_config;
CREATE TABLE poc.article_own_config AS
SELECT DISTINCT child_reference_id AS reference_id, child_level AS level,
       (child_level = 1 OR child_reference_id % 7 = 0) AS has_own_config,
       CASE WHEN child_level = 1 THEN 10.0
            WHEN child_reference_id % 7 = 0 THEN 5.0
            ELSE NULL END AS own_charges
FROM poc.dependant_articles;

-- Parte B: enfoque SQL/precomputado de HOY — usa config_source_chain ya materializada
-- para encontrar el ancestro más cercano con config, para UN artículo (10001)
WITH ranked AS (
    SELECT (chain_elem.value)::text::int AS ancestor_ref, chain_elem.ordinality
    FROM poc.propagation_tree pt
    CROSS JOIN LATERAL jsonb_array_elements(pt.config_source_chain) WITH ORDINALITY AS chain_elem(value, ordinality)
    WHERE pt.reference_id = 10001 AND pt.level = 4
)
SELECT r.ancestor_ref, c.own_charges
FROM ranked r
JOIN poc.article_own_config c ON c.reference_id = r.ancestor_ref
WHERE c.has_own_config
ORDER BY r.ordinality DESC
LIMIT 1;

-- Parte C: enfoque AGE/Cypher — MISMA pregunta, resuelta con traversal en vez de leer una
-- columna JSONB precalculada
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT * FROM cypher('precios_graph', $$
    MATCH path = (target:Articulo {ref_id: 10001, level: 4})-[:CHILD_OF*0..3]->(ancestor)
    WHERE ancestor.has_own_config = true
    RETURN ancestor.ref_id, length(path) AS dist
    ORDER BY dist ASC
    LIMIT 1
$$) AS (ref_id agtype, dist agtype);

-- Costo REAL de resolver esto "por request" (lo que pagarías cada vez que alguien pide un
-- precio, si se elimina la precomputación nocturna)
EXPLAIN ANALYZE
SELECT * FROM cypher('precios_graph', $$
    MATCH path = (target:Articulo {ref_id: 10001, level: 4})-[:CHILD_OF*0..3]->(ancestor)
    WHERE ancestor.has_own_config = true
    RETURN ancestor.ref_id, length(path) AS dist
    ORDER BY dist ASC
    LIMIT 1
$$) AS (ref_id agtype, dist agtype);

-- Parte D: resolver para TODOS los 90 artículos de nivel 4 en una sola consulta (el caso
-- "rebuild parcial de una zona", no un solo request) — SQL vs Cypher
EXPLAIN ANALYZE
WITH ranked AS (
    SELECT pt.reference_id AS target_ref, (chain_elem.value)::text::int AS ancestor_ref, chain_elem.ordinality
    FROM poc.propagation_tree pt
    CROSS JOIN LATERAL jsonb_array_elements(pt.config_source_chain) WITH ORDINALITY AS chain_elem(value, ordinality)
    WHERE pt.level = 4
),
best AS (
    SELECT DISTINCT ON (r.target_ref) r.target_ref, r.ancestor_ref, c.own_charges
    FROM ranked r
    JOIN poc.article_own_config c ON c.reference_id = r.ancestor_ref AND c.has_own_config
    ORDER BY r.target_ref, r.ordinality DESC
)
SELECT count(*) FROM best;

EXPLAIN ANALYZE
SELECT * FROM cypher('precios_graph', $$
    MATCH (target:Articulo {level: 4})
    MATCH path = (target)-[:CHILD_OF*0..3]->(ancestor)
    WHERE ancestor.has_own_config = true
    WITH target, min(length(path)) AS best_dist
    RETURN target.ref_id, best_dist
$$) AS (ref_id agtype, best_dist agtype);

-- Qué observar:
--   1. Parte B vs Parte C: ¿coinciden en el resultado? (validación de correctitud)
--   2. El EXPLAIN ANALYZE de "un solo artículo" (parte C repetida): ese es el costo POR
--      REQUEST si de verdad se elimina la precomputación nocturna y se resuelve al vuelo.
--   3. Parte D: al pedir los 90 de un jalón, ¿el plan de Cypher escala mejor o peor que el
--      SQL equivalente? Esto es la pregunta real pendiente de validar a escala de
--      producción (aquí es demasiado chico para ser concluyente, pero muestra la FORMA
--      del plan que habría que vigilar).
