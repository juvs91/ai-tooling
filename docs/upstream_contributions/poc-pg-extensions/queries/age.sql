-- Experimento 3: Apache AGE vs. WITH RECURSIVE. Correr en pg-combo (localhost:5461)
-- DESPUÉS de seed_relational.sql y seed_graph.sql.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Cypher: todos los descendientes del grupo estadístico 1 (todo su subárbol)
SELECT * FROM cypher('precios_graph', $$
    MATCH (raiz:Articulo {ref_id: 1, level: 1})<-[:CHILD_OF*1..3]-(descendiente)
    RETURN descendiente.ref_id, descendiente.level
$$) AS (ref_id agtype, level agtype);

-- Equivalente en SQL plano con WITH RECURSIVE sobre la misma tabla (como hoy en el
-- pipeline real de precios)
WITH RECURSIVE subtree AS (
    SELECT child_reference_id, child_level
    FROM poc.dependant_articles
    WHERE parent_reference_id = 1 AND parent_level = 1
    UNION ALL
    SELECT da.child_reference_id, da.child_level
    FROM poc.dependant_articles da
    JOIN subtree s ON da.parent_reference_id = s.child_reference_id
                   AND da.parent_level = s.child_level
)
SELECT * FROM subtree;

-- Comparar: EXPLAIN ANALYZE de ambas, y sobre todo la LEGIBILIDAD — ¿la versión Cypher
-- comunica mejor la intención ("descendientes de X") que el CTE recursivo con dos
-- condiciones de JOIN?

-- Segundo caso: jerarquía de entidades (DAG real, un consignado con 2 padres)
SELECT * FROM cypher('precios_graph', $$
    MATCH (c:Entidad {entity_type: 'CONSIGNADO'})-[:CHILD_OF]->(padre:Entidad)
    RETURN c.entity_id, collect(padre.entity_id)
$$) AS (entity_id agtype, padres agtype);
