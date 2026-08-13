-- Solo para pg-combo (AGE). Corre DESPUÉS de seed_relational.sql.
-- Carga como grafo lo mismo que ya existe en poc.dependant_articles y poc.entities, para
-- poder comparar Cypher vs. WITH RECURSIVE sobre el mismo dataset.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT create_graph('precios_graph');

-- Vértices de artículos (uno por reference_id+level distinto en dependant_articles)
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT DISTINCT child_reference_id AS ref_id, child_level AS level
        FROM poc.dependant_articles
    LOOP
        EXECUTE format($fmt$
            SELECT * FROM cypher('precios_graph', $$
                CREATE (:Articulo {ref_id: %1$s, level: %2$s})
            $$) AS (v agtype)
        $fmt$, r.ref_id, r.level);
    END LOOP;
END $do$;

-- Aristas CHILD_OF entre artículos (parent -> child)
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT parent_reference_id AS p_ref, parent_level AS p_level,
               child_reference_id AS c_ref, child_level AS c_level
        FROM poc.dependant_articles
        WHERE parent_reference_id IS NOT NULL
    LOOP
        EXECUTE format($fmt$
            SELECT * FROM cypher('precios_graph', $$
                MATCH (p:Articulo {ref_id: %1$s, level: %2$s}), (c:Articulo {ref_id: %3$s, level: %4$s})
                CREATE (c)-[:CHILD_OF]->(p)
            $$) AS (e agtype)
        $fmt$, r.p_ref, r.p_level, r.c_ref, r.c_level);
    END LOOP;
END $do$;

-- Vértices de entidades (CUC/cuenta/consignado)
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT DISTINCT entity_id, entity_type FROM poc.entities LOOP
        EXECUTE format($fmt$
            SELECT * FROM cypher('precios_graph', $$
                CREATE (:Entidad {entity_id: %1$s, entity_type: %2$s})
            $$) AS (v agtype)
        $fmt$, r.entity_id, quote_literal(r.entity_type));
    END LOOP;
END $do$;

-- Aristas CHILD_OF entre entidades (permite multi-padre => DAG real en el grafo)
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT entity_id, parent_entity_id
        FROM poc.entities
        WHERE parent_entity_id IS NOT NULL
    LOOP
        EXECUTE format($fmt$
            SELECT * FROM cypher('precios_graph', $$
                MATCH (c:Entidad {entity_id: %1$s}), (p:Entidad {entity_id: %2$s})
                CREATE (c)-[:CHILD_OF]->(p)
            $$) AS (e agtype)
        $fmt$, r.entity_id, r.parent_entity_id);
    END LOOP;
END $do$;
