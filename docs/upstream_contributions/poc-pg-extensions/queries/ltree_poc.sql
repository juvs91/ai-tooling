-- POC ltree: modelar dependant_articles (árbol real) + geographic_zones (árbol chico
-- sintético) + entities (DAG real, multi-path) como ltree, y probar el diseño de
-- "convergence_path" (org_scope || region_scope || ge_scope) discutido en la conversación
-- sobre ltree. Correr en pg-combo (5461) — ltree es contrib estándar, no necesita
-- Citus/AGE, ya viene con Postgres.

CREATE EXTENSION IF NOT EXISTS ltree;
CREATE SCHEMA IF NOT EXISTS ltree_poc;

-- 1) ge_articulo: dependant_articles (SÍ es árbol — confirmado, 1 padre por fila) → ltree
DROP TABLE IF EXISTS ltree_poc.ge_articulo;
CREATE TABLE ltree_poc.ge_articulo (
    reference_id int PRIMARY KEY,
    level int,
    path ltree NOT NULL
);

WITH RECURSIVE built AS (
    SELECT child_reference_id AS reference_id, child_level AS level,
           ('n' || child_reference_id)::ltree AS path
    FROM poc.dependant_articles
    WHERE child_level = 1
    UNION ALL
    SELECT da.child_reference_id, da.child_level,
           b.path || ('n' || da.child_reference_id)::ltree
    FROM poc.dependant_articles da
    JOIN built b ON da.parent_reference_id = b.reference_id AND da.parent_level = b.level
)
INSERT INTO ltree_poc.ge_articulo SELECT * FROM built;

CREATE INDEX ge_articulo_path_gist ON ltree_poc.ge_articulo USING GIST (path);

-- 2) region_node: geographic_zones — chico y sintético (país → 4 regiones → 12 sucursales)
DROP TABLE IF EXISTS ltree_poc.region_node;
CREATE TABLE ltree_poc.region_node (
    zone_id int PRIMARY KEY,
    path ltree NOT NULL
);
INSERT INTO ltree_poc.region_node (zone_id, path) VALUES
    (1,   'z1'),
    (11,  'z1.z11'), (12, 'z1.z12'), (13, 'z1.z13'), (14, 'z1.z14'),
    (111, 'z1.z11.z111'), (112, 'z1.z11.z112'), (113, 'z1.z11.z113'),
    (121, 'z1.z12.z121'), (122, 'z1.z12.z122'), (123, 'z1.z12.z123'),
    (131, 'z1.z13.z131'), (132, 'z1.z13.z132'), (133, 'z1.z13.z133'),
    (141, 'z1.z14.z141'), (142, 'z1.z14.z142'), (143, 'z1.z14.z143');

CREATE INDEX region_node_path_gist ON ltree_poc.region_node USING GIST (path);

-- 3) entity_paths: entities SÍ es DAG (confirmado: consignado 1001 tiene 2 padres) →
--    multi-path materialization (una fila por cada cadena de ancestros válida)
DROP TABLE IF EXISTS ltree_poc.entity_paths;
CREATE TABLE ltree_poc.entity_paths (
    entity_id int NOT NULL,
    path ltree NOT NULL,
    PRIMARY KEY (entity_id, path)
);

WITH RECURSIVE built AS (
    SELECT entity_id, ('e' || entity_id)::ltree AS path
    FROM poc.entities WHERE parent_entity_id IS NULL
    UNION ALL
    SELECT e.entity_id, b.path || ('e' || e.entity_id)::ltree
    FROM poc.entities e
    JOIN built b ON e.parent_entity_id = b.entity_id
)
INSERT INTO ltree_poc.entity_paths SELECT DISTINCT entity_id, path FROM built;

CREATE INDEX entity_paths_gist ON ltree_poc.entity_paths USING GIST (path);

-- Confirmar el caso DAG: el consignado 1001 debe tener 2 filas (2 caminos válidos)
SELECT * FROM ltree_poc.entity_paths WHERE entity_id = 1001;

-- 4) regla_cruce: la tabla de reglas con convergence_path generado
DROP TABLE IF EXISTS ltree_poc.regla_cruce;
CREATE TABLE ltree_poc.regla_cruce (
    id serial PRIMARY KEY,
    org_scope ltree,
    region_scope ltree,
    ge_scope ltree,
    convergence_path ltree GENERATED ALWAYS AS (
        coalesce(org_scope, ''::ltree) || coalesce(region_scope, ''::ltree) || coalesce(ge_scope, ''::ltree)
    ) STORED,
    cade_id int,
    descripcion text
);

INSERT INTO ltree_poc.regla_cruce (org_scope, region_scope, ge_scope, cade_id, descripcion) VALUES
    (NULL,        NULL,          NULL,           1, 'default global'),
    (NULL,        'z1.z11',      NULL,           2, 'toda la region Norte (z11)'),
    (NULL,        NULL,          'n1',           3, 'todo el grupo estadistico 1'),
    ('e1',        'z1.z11',      'n1',           4, 'CUC 1 + region Norte + GE 1 (especifica)'),
    (NULL,        'z1.z12',      'n1.n101',      5, 'region Centro (z12) + familia 101'),
    ('e2',        NULL,          NULL,           6, 'CUC 2, toda region/GE');

CREATE INDEX regla_cruce_convergence_gist ON ltree_poc.regla_cruce USING GIST (convergence_path);

SELECT id, convergence_path, nlevel(convergence_path), descripcion FROM ltree_poc.regla_cruce ORDER BY id;
