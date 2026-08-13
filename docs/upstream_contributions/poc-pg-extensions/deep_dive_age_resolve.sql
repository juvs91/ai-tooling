-- DEEP DIVE (aprendizaje, no producción): ¿se puede reemplazar CADE precomputada por
-- resolución "al vuelo" vía grafo? Corre en pg-combo (localhost:5461) DESPUÉS de
-- seed_relational.sql + seed_graph.sql.
--
-- Idea: hoy propagation_tree.config_source_chain PRECOMPUTA, para cada nodo, cuál es su
-- ancestro más cercano con config propia (la mayoría de nodos NO tienen config propia,
-- heredan). Aquí simulamos ese mismo dato como propiedades del grafo y resolvemos la
-- misma pregunta con un traversal Cypher en vez de leer una columna JSONB precalculada.

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- 1) Simular "config propia" sobre el grafo ya cargado: todo nivel 1 (grupo estadístico) y
--    todo CUC tiene config propia (son las raíces, deben tenerla). Además, un ~15% del
--    resto también tiene override propio (patrón realista: la mayoría hereda).
SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo {level: 1})
    SET a.has_own_config = true, a.own_charges = 10.0
$$) AS (a agtype);

SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo)
    WHERE a.level <> 1 AND a.ref_id % 7 = 0
    SET a.has_own_config = true, a.own_charges = 5.0
$$) AS (a agtype);

SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo)
    WHERE a.has_own_config IS NULL
    SET a.has_own_config = false
$$) AS (a agtype);

SELECT * FROM cypher('precios_graph', $$
    MATCH (e:Entidad {entity_type: 'CUC'})
    SET e.has_own_config = true, e.own_charges = 2.0
$$) AS (e agtype);

SELECT * FROM cypher('precios_graph', $$
    MATCH (e:Entidad)
    WHERE e.entity_type <> 'CUC' AND e.entity_id % 7 = 0
    SET e.has_own_config = true, e.own_charges = 1.0
$$) AS (e agtype);

SELECT * FROM cypher('precios_graph', $$
    MATCH (e:Entidad)
    WHERE e.has_own_config IS NULL
    SET e.has_own_config = false
$$) AS (e agtype);

-- Cuántos nodos quedaron con config propia (para saber qué tan "sparse" es, como en la
-- realidad: la mayoría de dependant_articles NO tiene override, por eso existe
-- config_source_chain en primer lugar)
SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo) WHERE a.has_own_config = true RETURN count(a)
$$) AS (con_config agtype);
