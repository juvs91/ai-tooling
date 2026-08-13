-- Datos 100% sintéticos (ids/nombres inventados) — NUNCA datos reales de Deacero.
-- Corre igual en pg-combo (Citus) y pg-hydra: crea las 4 estructuras jerárquicas/closure
-- del dominio de precios, simplificadas, para comparar el mismo dataset entre motores.
-- Volumen objetivo: dependant_articles ~135, entities ~85, propagation_tree ~135,
-- cross_all_discounts_with_entities ~100 filas (rangos "un par de cientos" pedidos).

CREATE SCHEMA IF NOT EXISTS poc;

-- 1) dependant_articles: adjacency list, 4 niveles
--    nivel 1 = grupo_estadistico, 2 = familia, 3 = subfamilia, 4 = articulo
DROP TABLE IF EXISTS poc.dependant_articles;
CREATE TABLE poc.dependant_articles (
    id                  serial PRIMARY KEY,
    parent_reference_id integer,
    parent_level        integer,
    child_reference_id  integer NOT NULL,
    child_level         integer NOT NULL,
    unit_price          numeric(12,2) NOT NULL,
    base_price          numeric(12,2) NOT NULL
);

-- 5 grupos estadísticos (1-5), 3 familias c/u (101-115), 2 subfamilias c/u (1001-1030),
-- 3 artículos c/u (10001-10090).
INSERT INTO poc.dependant_articles (parent_reference_id, parent_level, child_reference_id, child_level, unit_price, base_price)
SELECT NULL, NULL, g, 1, 0, 0
FROM generate_series(1, 5) AS g;

INSERT INTO poc.dependant_articles (parent_reference_id, parent_level, child_reference_id, child_level, unit_price, base_price)
SELECT ((f - 101) / 3) + 1, 1, f, 2, 0, 0
FROM generate_series(101, 115) AS f;

INSERT INTO poc.dependant_articles (parent_reference_id, parent_level, child_reference_id, child_level, unit_price, base_price)
SELECT ((s - 1001) / 2) + 101, 2, s, 3, 0, 0
FROM generate_series(1001, 1030) AS s;

INSERT INTO poc.dependant_articles (parent_reference_id, parent_level, child_reference_id, child_level, unit_price, base_price)
SELECT ((a - 10001) / 3) + 1001, 3, a, 4, round((10 + random() * 90)::numeric, 2), round((8 + random() * 70)::numeric, 2)
FROM generate_series(10001, 10090) AS a;

-- 2) entities: CUC -> cuenta -> consignado, con casos multi-padre (DAG real)
DROP TABLE IF EXISTS poc.entities;
CREATE TABLE poc.entities (
    id               serial PRIMARY KEY,
    entity_id        integer NOT NULL,
    entity_type      text NOT NULL,
    parent_entity_id integer
);

INSERT INTO poc.entities (entity_id, entity_type, parent_entity_id)
SELECT c, 'CUC', NULL FROM generate_series(1, 8) AS c;

INSERT INTO poc.entities (entity_id, entity_type, parent_entity_id)
SELECT 100 + a, 'CUENTA', ((a - 1) / 3) + 1
FROM generate_series(1, 24) AS a;

INSERT INTO poc.entities (entity_id, entity_type, parent_entity_id)
SELECT 1000 + n, 'CONSIGNADO', 100 + ((n - 1) / 2) + 1
FROM generate_series(1, 48) AS n;

-- Caso DAG real: 5 consignados que además cuelgan de una segunda cuenta (multi-padre)
INSERT INTO poc.entities (entity_id, entity_type, parent_entity_id)
SELECT 1000 + n, 'CONSIGNADO', 100 + ((n - 1) / 2) + 2
FROM generate_series(1, 5) AS n;

-- 3) propagation_tree: closure table materializada (root + cadena de ancestros config)
DROP TABLE IF EXISTS poc.propagation_tree;
CREATE TABLE poc.propagation_tree (
    id                    serial PRIMARY KEY,
    run_id                uuid NOT NULL,
    reference_id          integer NOT NULL,
    level                 integer NOT NULL,
    depth                 integer NOT NULL,
    parent_reference_id   integer,
    parent_level          integer,
    root_reference_id     integer NOT NULL,
    root_level            integer NOT NULL DEFAULT 1,
    config_source_chain   jsonb NOT NULL
);

WITH RECURSIVE closure AS (
    SELECT child_reference_id AS reference_id, child_level AS level, 0 AS depth,
           parent_reference_id, parent_level,
           child_reference_id AS root_reference_id,
           jsonb_build_array(child_reference_id) AS config_source_chain
    FROM poc.dependant_articles
    WHERE child_level = 1
    UNION ALL
    SELECT da.child_reference_id, da.child_level, c.depth + 1,
           da.parent_reference_id, da.parent_level,
           c.root_reference_id,
           c.config_source_chain || jsonb_build_array(da.child_reference_id)
    FROM poc.dependant_articles da
    JOIN closure c ON da.parent_reference_id = c.reference_id AND da.parent_level = c.level
)
INSERT INTO poc.propagation_tree (run_id, reference_id, level, depth, parent_reference_id, parent_level, root_reference_id, config_source_chain)
SELECT '00000000-0000-0000-0000-000000000001'::uuid, reference_id, level, depth, parent_reference_id, parent_level, root_reference_id, config_source_chain
FROM closure;

-- 4) cross_all_discounts_with_entities (CADE) simplificada, ligada a propagation_tree
DROP TABLE IF EXISTS poc.cross_all_discounts_with_entities;
CREATE TABLE poc.cross_all_discounts_with_entities (
    id                          serial PRIMARY KEY,
    reference_id                integer NOT NULL,
    entity_id                   integer,
    zone_id                     integer NOT NULL,
    currency_code               text NOT NULL DEFAULT 'MXN',
    charges_and_discounts_json  jsonb NOT NULL,
    status                      smallint NOT NULL DEFAULT 1
);

INSERT INTO poc.cross_all_discounts_with_entities (reference_id, entity_id, zone_id, charges_and_discounts_json)
SELECT
    pt.reference_id,
    (SELECT entity_id FROM poc.entities ORDER BY random() LIMIT 1),
    1 + (row_number() OVER () % 4),
    jsonb_build_object(
        'charges', jsonb_build_array(jsonb_build_object('type', 'flete', 'value', round((random() * 50)::numeric, 2))),
        'discounts', jsonb_build_array(jsonb_build_object('type', 'volumen', 'value', round((random() * 10)::numeric, 2)))
    )
FROM poc.propagation_tree pt
WHERE pt.level = 4
LIMIT 100;
