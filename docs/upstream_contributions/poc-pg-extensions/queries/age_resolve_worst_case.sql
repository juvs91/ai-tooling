-- DEEP DIVE (caso preocupante): resolver la config efectiva para UN consignado con
-- múltiples padres (DAG real, no árbol) × 90 artículos de una sola pasada — el caso real
-- de "un cliente pide su lista de precios completa". Correr en pg-combo (5461) DESPUÉS de
-- deep_dive_age_resolve.sql y queries/age_resolve_deep_dive.sql.
--
-- Consignado elegido: entity_id 1001, que cuelga de DOS padres (cuentas 101 y 102) — el
-- caso DAG real, no un árbol simple.

-- Parte A: equivalente SQL — poc.entity_own_config con la MISMA regla que se aplicó en el
-- grafo (CUC siempre tiene config propia; el resto, 1 de cada 7)
DROP TABLE IF EXISTS poc.entity_own_config;
CREATE TABLE poc.entity_own_config AS
SELECT DISTINCT entity_id,
       (entity_type = 'CUC' OR entity_id % 7 = 0) AS has_own_config,
       CASE WHEN entity_type = 'CUC' THEN 2.0
            WHEN entity_id % 7 = 0 THEN 1.0
            ELSE NULL END AS own_charges
FROM poc.entities;

-- Ancestro(s) más cercano(s) con config, para el consignado 1001, explorando AMBAS ramas
-- del DAG (BFS multi-padre real, no un simple "sube por un solo padre")
WITH RECURSIVE ancestors AS (
    SELECT entity_id, parent_entity_id AS ancestor_id, 1 AS depth
    FROM poc.entities
    WHERE entity_id = 1001 AND parent_entity_id IS NOT NULL
    UNION ALL
    SELECT a.entity_id, e.parent_entity_id, a.depth + 1
    FROM ancestors a
    JOIN poc.entities e ON e.entity_id = a.ancestor_id
    WHERE e.parent_entity_id IS NOT NULL AND a.depth < 4
)
SELECT a.ancestor_id, a.depth, c.own_charges
FROM ancestors a
JOIN poc.entity_own_config c ON c.entity_id = a.ancestor_id AND c.has_own_config
ORDER BY a.depth ASC;

-- Parte B: equivalente Cypher — misma pregunta, explorando el DAG completo
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT * FROM cypher('precios_graph', $$
    MATCH path = (target:Entidad {entity_id: 1001})-[:CHILD_OF*1..4]->(ancestor)
    WHERE ancestor.has_own_config = true
    RETURN ancestor.entity_id, length(path)
    ORDER BY length(path) ASC
$$) AS (entity_id agtype, dist agtype);

-- Parte C (EL CASO PREOCUPANTE): para el consignado 1001, resolver la config de los 90
-- artículos de nivel 4 EN UNA SOLA CONSULTA — esto es "cliente pide su lista de precios
-- completa", combinando el traversal del DAG de entidades CON el traversal de artículos.

-- C.1 — SQL: recursive CTE de entidad (una vez) cross join con la config_source_chain ya
-- materializada de cada artículo (lo que hoy hace el pipeline real)
EXPLAIN ANALYZE
WITH RECURSIVE entity_ancestors AS (
    SELECT entity_id, parent_entity_id AS ancestor_id, 1 AS depth
    FROM poc.entities WHERE entity_id = 1001 AND parent_entity_id IS NOT NULL
    UNION ALL
    SELECT a.entity_id, e.parent_entity_id, a.depth + 1
    FROM entity_ancestors a
    JOIN poc.entities e ON e.entity_id = a.ancestor_id
    WHERE e.parent_entity_id IS NOT NULL AND a.depth < 4
),
entity_config AS (
    SELECT a.ancestor_id, a.depth, c.own_charges
    FROM entity_ancestors a
    JOIN poc.entity_own_config c ON c.entity_id = a.ancestor_id AND c.has_own_config
    ORDER BY a.depth ASC LIMIT 1
),
article_ancestors AS (
    SELECT pt.reference_id AS article_ref, (chain_elem.value)::text::int AS ancestor_ref, chain_elem.ordinality
    FROM poc.propagation_tree pt
    CROSS JOIN LATERAL jsonb_array_elements(pt.config_source_chain) WITH ORDINALITY AS chain_elem(value, ordinality)
    WHERE pt.level = 4
),
article_config AS (
    SELECT DISTINCT ON (r.article_ref) r.article_ref, r.ancestor_ref, c.own_charges
    FROM article_ancestors r
    JOIN poc.article_own_config c ON c.reference_id = r.ancestor_ref AND c.has_own_config
    ORDER BY r.article_ref, r.ordinality DESC
)
SELECT ac.article_ref, ac.own_charges AS article_charge, ec.own_charges AS entity_charge
FROM article_config ac, entity_config ec;

-- C.2 — Cypher: mismo caso, resolver los 90 artículos combinados con el traversal del DAG
-- de la entidad, en una sola consulta
EXPLAIN ANALYZE
SELECT * FROM cypher('precios_graph', $$
    MATCH path_e = (e:Entidad {entity_id: 1001})-[:CHILD_OF*1..4]->(anc_e)
    WHERE anc_e.has_own_config = true
    WITH e, min(length(path_e)) AS entity_dist
    MATCH (a:Articulo {level: 4})
    MATCH path_a = (a)-[:CHILD_OF*0..3]->(anc_a)
    WHERE anc_a.has_own_config = true
    WITH a, entity_dist, min(length(path_a)) AS article_dist
    RETURN a.ref_id, article_dist, entity_dist
$$) AS (ref_id agtype, article_dist agtype, entity_dist agtype);

-- Qué observar:
--   1. Parte A vs Parte B: ¿el SQL recursivo y Cypher concuerdan en qué ancestro(s) de
--      entidad tienen config, y a qué profundidad, considerando las 2 ramas del DAG?
--   2. C.1 vs C.2: tiempos — este es el caso real de "un cliente pide su lista de precios
--      completa" (90 artículos × 1 entidad con DAG), no un solo artículo aislado.
--   3. ¿El plan de C.2 muestra el mismo patrón de "explora todo, filtra después" que ya
--      vimos en queries/age_resolve_deep_dive.sql, ahora agravado por combinar DOS
--      traversals de profundidad variable en la misma consulta?
