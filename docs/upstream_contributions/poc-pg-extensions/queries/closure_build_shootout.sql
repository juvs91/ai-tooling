-- SHOOTOUT: ¿quién construye el cierre transitivo completo ("ancestro más cercano con
-- config propia", para LOS 140 Articulo, no solo 90) más barato? 4 formas de resolver
-- exactamente el mismo problema. Correr en pg-combo (5461) después de
-- deep_dive_age_resolve.sql.

-- ============================================================================
-- 1) SQL — recursive CTE de UNA sola pasada, multi-fuente (siembra desde TODOS los nodos
--    con config propia a la vez) + DISTINCT ON para que gane el ancestro más cercano.
--    Esta es la respuesta a "cómo convertirlo en una closure table en un solo query": se
--    siembra desde todas las fuentes en paralelo, se deja que la recursión choque, y al
--    final se resuelve la competencia por distancia mínima.
-- ============================================================================
EXPLAIN ANALYZE
WITH RECURSIVE closure AS (
    SELECT reference_id, level, reference_id AS anc_ref, 0 AS dist, own_charges
    FROM poc.article_own_config
    WHERE has_own_config
    UNION ALL
    SELECT da.child_reference_id, da.child_level, c.anc_ref, c.dist + 1, c.own_charges
    FROM poc.dependant_articles da
    JOIN closure c ON da.parent_reference_id = c.reference_id AND da.parent_level = c.level
)
SELECT DISTINCT ON (reference_id, level) reference_id, level, anc_ref, dist, own_charges
INTO TEMP closure_sql_oneshot
FROM closure
ORDER BY reference_id, level, dist ASC;

SELECT count(*) FROM closure_sql_oneshot;

-- ============================================================================
-- 2) SQL — iterativo por niveles con tabla de estado (el patrón REAL que ya existe en el
--    pipeline: fn_propagate_by_depth, "FOR v_current_depth IN 0..max LOOP")
-- ============================================================================
DROP TABLE IF EXISTS poc.closure_iter;
CREATE TABLE poc.closure_iter (
    reference_id integer, level integer, anc_ref integer, dist integer, own_charges numeric,
    PRIMARY KEY (reference_id, level)
);

DO $do$
DECLARE
    d int := 0;
    rows_inserted int;
    t_start timestamptz := clock_timestamp();
BEGIN
    INSERT INTO poc.closure_iter
    SELECT reference_id, level, reference_id, 0, own_charges
    FROM poc.article_own_config WHERE has_own_config;

    LOOP
        INSERT INTO poc.closure_iter (reference_id, level, anc_ref, dist, own_charges)
        SELECT da.child_reference_id, da.child_level, r.anc_ref, r.dist + 1, r.own_charges
        FROM poc.dependant_articles da
        JOIN poc.closure_iter r ON da.parent_reference_id = r.reference_id
                                AND da.parent_level = r.level AND r.dist = d
        WHERE NOT EXISTS (
            SELECT 1 FROM poc.closure_iter x
            WHERE x.reference_id = da.child_reference_id AND x.level = da.child_level
        );
        GET DIAGNOSTICS rows_inserted = ROW_COUNT;
        EXIT WHEN rows_inserted = 0 OR d > 10;
        d := d + 1;
    END LOOP;
    RAISE NOTICE 'SQL iterativo: % niveles, % ms', d, extract(milliseconds from clock_timestamp() - t_start);
END $do$;

SELECT count(*) FROM poc.closure_iter;

-- ============================================================================
-- 3) Cypher — "naive", UNA sola consulta declarativa con *0..N (lo mismo que ya se probó
--    para 90 nodos, ahora para los 140 completos)
-- ============================================================================
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

EXPLAIN ANALYZE
SELECT * FROM cypher('precios_graph', $$
    MATCH (target:Articulo)
    MATCH path = (target)-[:CHILD_OF*0..3]->(ancestor)
    WHERE ancestor.has_own_config = true
    WITH target, min(length(path)) AS best_dist
    RETURN target.ref_id, best_dist
$$) AS (ref_id agtype, best_dist agtype);

-- ============================================================================
-- 4) Cypher — iterativo por niveles (MISMO algoritmo que la opción 2, pero expandiendo la
--    frontera con SET sobre propiedades del grafo en vez de INSERT en una tabla SQL)
-- ============================================================================
SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo) WHERE a.has_own_config = true
    SET a.resolved_dist = 0, a.resolved_anc = a.ref_id
$$) AS (a agtype);

DO $do$
DECLARE
    d int := 0;
    stmt text;
    rows_affected int;
    t_start timestamptz := clock_timestamp();
BEGIN
    LOOP
        stmt := format($fmt$
            SELECT * FROM cypher('precios_graph', $$
                MATCH (child:Articulo)-[:CHILD_OF]->(parent:Articulo)
                WHERE parent.resolved_dist = %1$s AND child.resolved_dist IS NULL
                SET child.resolved_dist = %2$s, child.resolved_anc = parent.resolved_anc
            $$) AS (child agtype)
        $fmt$, d, d + 1);
        EXECUTE stmt;
        GET DIAGNOSTICS rows_affected = ROW_COUNT;
        EXIT WHEN rows_affected = 0 OR d > 10;
        d := d + 1;
    END LOOP;
    RAISE NOTICE 'Cypher iterativo: % niveles, % ms', d, extract(milliseconds from clock_timestamp() - t_start);
END $do$;

SELECT * FROM cypher('precios_graph', $$
    MATCH (a:Articulo) WHERE a.resolved_dist IS NOT NULL RETURN count(a)
$$) AS (resueltos agtype);

-- Qué observar: los NOTICE de las opciones 2 y 4 dan el tiempo real de construir el
-- cierre completo con el algoritmo CORRECTO (BFS por niveles, cada arista visitada una
-- sola vez) en cada motor — esa es la comparación justa. Las opciones 1 y 3 son los
-- "intentos de una sola consulta" (1 sí funciona razonablemente en SQL gracias a
-- DISTINCT ON; 3 ya sabemos que expande de más).
