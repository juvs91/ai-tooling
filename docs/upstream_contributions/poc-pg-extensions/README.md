# POC local: Citus columnar + Apache AGE, y Hydra Columnar aparte

POC 100% local para aprender de primera mano cómo se comportan estas extensiones —
**no toca AlloyDB/Cloud SQL reales**, y usa solo datos sintéticos (nunca datos de Deacero).
Contexto completo del análisis previo (por qué no se puede instalar esto en Cloud
SQL/AlloyDB gestionados, por qué cstore_fdw quedó fuera) en el plan de la sesión que originó
este POC.

## Por qué Citus + AGE juntos, y Hydra aparte

El objetivo original era combinar Hydra Columnar + AGE en una sola instancia, pero el build
real de Hydra usa `pgxman` y artefactos internos de su CI (`COPY --from=columnar /pg_ext /`
en su Dockerfile) — no es un `make install` reproducible a mano. Fallback aplicado: `pg-combo`
usa **Citus** (instalación estándar vía apt) + **Apache AGE** (build estándar desde fuente).
`pg-hydra` corre aparte con la imagen oficial de Hydra, solo para comparar los dos motores
columnar entre sí — no forma parte del experimento "combinado".

**Nota técnica:** Citus-columnar y Hydra-columnar no son complementarias — Hydra nació como
una extracción del mismo código columnar de Citus. No tiene sentido combinarlas entre sí
(competirían por el mismo table access method); por eso vive cada una en su propio
contenedor.

## Levantar el entorno

```bash
cd poc-pg-extensions
docker compose up -d --build
docker compose ps   # confirmar ambos "healthy"/"running"
```

- `pg-combo` (Citus + AGE): `localhost:5461`, db `poc`, user `poc`, password `poc`
- `pg-hydra`: `localhost:5462`, db `poc`, user `poc`, password `poc`

## Cargar los datos sintéticos

```bash
psql "postgresql://poc:poc@localhost:5461/poc" -f seed_relational.sql
psql "postgresql://poc:poc@localhost:5461/poc" -f seed_graph.sql   # solo pg-combo (AGE)

psql "postgresql://poc:poc@localhost:5462/poc" -f seed_relational.sql   # también en pg-hydra
```

## Conectar por DBeaver

Conexión Postgres estándar contra cada puerto (5461 / 5462), mismas credenciales de arriba.
Para usar Cypher en `pg-combo` (AGE), cada sesión nueva de DBeaver necesita correr primero:

```sql
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
```

(ya queda seteado como default de la base vía `ALTER DATABASE poc SET search_path = ...` en
`combo/init.sql`, pero DBeaver puede cachear la sesión — si una query con `cypher(...)` falla
con "function cypher does not exist", correr las dos líneas de arriba manualmente.)

## Experimentos (correr en este orden)

| Archivo | Dónde | Qué prueba |
|---|---|---|
| `queries/citus.sql` | pg-combo (5461) | Columnar Citus: tamaño, agregación vs row-store, fricción de `UPDATE` |
| `queries/hydra.sql` | pg-hydra (5462) | Mismo experimento con Hydra — comparar contra Citus |
| `queries/age.sql` | pg-combo (5461) | Cypher (AGE) vs. `WITH RECURSIVE` sobre la misma jerarquía |
| `queries/combined.sql` | pg-combo (5461) | AGE (topología) + Citus columnar (agregación) en una sola consulta — ¿se complementan de verdad? |

Cada archivo trae comentarios de qué observar en cada paso (EXPLAIN ANALYZE, tamaño en
disco, si el `UPDATE` corre o falla, legibilidad de Cypher vs. SQL recursivo).

## Preguntas que este POC responde en la práctica

1. **"Insert-only con versión es básicamente MVCC de Postgres, ¿migrar es fácil?"** — MVCC
   versiona por tupla (una fila); columnar versiona por stripe comprimido (miles de filas).
   `queries/citus.sql` y `queries/hydra.sql` muestran el costo real de un `UPDATE` de una
   sola fila en cada motor.
2. **"¿Qué tanto mejora/simplifica AGE nuestras jerarquías?"** — `queries/age.sql` compara,
   con el mismo dataset, Cypher contra el `WITH RECURSIVE` que ya usa el pipeline real.
3. **"¿Se complementan las extensiones entre sí?"** — `queries/combined.sql` es la prueba
   directa: grafo para topología + columnar para agregación de hechos, en una consulta.

## Conclusiones (corrida real, con las ~90-140 filas sintéticas de este POC)

- **Citus columnar** — comprimió a 24kB vs 56kB de la tabla row-store gemela (~2.3x, y eso
  que aquí solo hay 90 filas). Pero en la agregación `GROUP BY zone_id`, a este volumen tan
  chico el row-store fue **más rápido** (0.56ms vs 9.7ms) — el `ColumnarScan` tiene overhead
  de planeación/decodificación que solo se amortiza con muchas más filas; con 90 no hay nada
  que amortizar. **Lo más importante: el `UPDATE` de una fila falló** con
  `UPDATE and CTID scans not supported for ColumnarScan` — Citus columnar **rechaza de
  raíz** el UPDATE fila-por-fila. Esto confirma en la práctica, no en teoría, por qué
  columnar clásico no encaja con el patrón UPSERT del pipeline real de precios.

- **Hydra columnar** — sorpresa real: **si soportó el `UPDATE`** (`UPDATE 1`, sin error) —
  comportamiento opuesto al de Citus para el mismo tipo de operación. Segunda sorpresa: la
  imagen oficial de Hydra trae `default_table_access_method = columnar` **a nivel de base
  de datos** — la tabla "row-store gemela" que creé sin `USING columnar` terminó siendo
  columnar de todos modos (confirmado con `SELECT relname, amname FROM pg_class...`), así
  que la comparación de tamaño no fue válida en este motor (ambas dieron 24kB, son la misma
  cosa). Esto es un gotcha operativo real: si alguna vez se prueba Hydra en serio, hay que
  fijar `default_table_access_method = heap` explícitamente para poder comparar de verdad.

- **AGE vs. `WITH RECURSIVE`** — ambos devolvieron exactamente los mismos 27
  descendientes del grupo estadístico 1, coincidiendo fila por fila. La consulta Cypher
  (`MATCH (raiz)<-[:CHILD_OF*1..3]-(d)`) es más corta y comunica mejor la intención que el
  CTE recursivo (que necesita dos condiciones de JOIN por el par `reference_id`+`level`).
  El caso DAG (consignados con 2 padres) se resolvió limpio con `collect()` — se ve
  claramente `1001 | [102, 101]` (dos padres reales) en el resultado.

- **Combinado (AGE + Citus)** — sí funcionó, con un detalle técnico importante: AGE
  internamente guarda los vértices/aristas en **tablas Postgres normales**
  (`_ag_label_vertex`, `"Articulo"`, `"Entidad"` — visibles en el plan de `EXPLAIN`), así que
  no es una "caja negra" separada — es SQL por debajo. Pero el plan real mostró un
  `Nested Loop` con `age_match_vle_terminal_edge` y **2412 filas descartadas por el join
  filter** (de ~2500 evaluadas) para encontrar 90 matches — eficiente a esta escala (8.5ms)
  pero es la clase de patrón (nested loop + expansión de camino variable) que hay que
  vigilar de cerca si esto se probara con los volúmenes reales (CADE 13-129M filas): no hay
  garantía de que escale igual de bien solo porque funcionó aquí.

- **Veredicto para llevar de vuelta al análisis de arquitectura:**
  1. La fricción de UPDATE en columnar (predicha en la teoría) se confirmó **en un motor
     (Citus) y no en el otro (Hydra)** — no es una regla universal de "columnar = sin
     updates", depende de la implementación. Esto matiza el análisis original: Hydra podría
     ser viable para tablas con updates ocasionales donde Citus no lo sería — pero sigue sin
     poder instalarse en Cloud SQL/AlloyDB gestionados (bloqueador ya documentado).
  2. AGE cumple lo que prometía para las jerarquías — más legible, mismo resultado — pero el
     combinado con columnar mostró un patrón de join que merece un benchmark a escala real
     antes de asumir que "se complementan" sin fricción.
  3. Ninguno de estos hallazgos cambia la conclusión de fondo del análisis previo: la
     pregunta de negocio (¿vale la pena el costo de operar Postgres autoadministrado en
     GCE/GKE para poder usar estas extensiones?) sigue abierta y es independiente de que
     técnicamente funcionen — que es justo lo que este POC quería aislar y sí logró aislar.

## Deep dive: ¿reemplazar CADE precomputada por resolución "al vuelo" con AGE?

Archivos: `deep_dive_age_resolve.sql` (marca ~25 de 220 nodos con "config propia", simulando
que la mayoría hereda — igual que en la realidad) y
`queries/age_resolve_deep_dive.sql` (la comparación).

**Idea probada**: en vez de que el nocturno precalcule `propagation_tree.config_source_chain`
para TODO el árbol, resolver "¿cuál es el ancestro más cercano con config propia?" con un
traversal Cypher al momento de cada request — para ver si eso permite dejar de precomputar
CADE completa.

**Correctitud**: ✅ coincide. Para el artículo 10001, SQL (leyendo `config_source_chain` ya
materializada) y Cypher (traversal en vivo) dieron el mismo ancestro (nodo 1001, distancia 1).

**Performance — un solo target** (el costo "por request" si se elimina la precomputación):
~11-40ms por resolución (varió entre corridas; ver nota de escala abajo). Nada instantáneo,
pero tampoco prohibitivo para un request de API individual.

**Performance — los 90 artículos de nivel 4 en una sola consulta** (el caso "rebuild parcial
de una zona"):
- SQL (usando `config_source_chain` ya precomputada): **8.5ms**
- Cypher (traversal en vivo para los 90 a la vez): **280ms** — **33x más lento**

**Intenté arreglarlo con índices** (`CREATE INDEX ... USING gin(properties)` sobre los
vértices) — no cambió nada, el planner siguió usando Seq Scan en todos lados. A 140-220
filas eso es la decisión CORRECTA del planner (un índice no gana contra un seq scan en una
tabla tan chica) — así que este resultado **no prueba que indexar no ayudaría a escala
real**, solo que este dataset es demasiado pequeño para que la pregunta tenga respuesta.

**El hallazgo que sí importa, independiente de la escala**: mirando el plan con lupa, el
`Nested Loop` que resuelve `[:CHILD_OF*0..3]` (el traversal de profundidad variable) primero
expande TODOS los caminos posibles hasta 3 saltos desde cada target, y **hasta después**
aplica el filtro `ancestor.has_own_config = true` vía `Join Filter` — descartando ~15,350 de
~15,480 combinaciones evaluadas (99%+ de desperdicio) en la consulta batch. Es decir, esta
versión de AGE no empuja el filtro del ancestro hacia adentro del traversal — explora el
vecindario completo primero, filtra después.

Eso es relevante para el caso real: `propagation_tree` (la closure table que existe HOY)
fue construida específicamente para evitar este patrón — antes de que existiera, un CTE
recursivo directo sobre `dependant_articles` (145GB) causó un **OOM real** en producción
(migración `y2z3a4b5c6d7`). Un traversal Cypher de profundidad variable sin filtro empujado
hacia adentro es estructuralmente el mismo tipo de "explora todo, filtra después" que causó
ese OOM — solo que en Cypher en vez de SQL recursivo. Conclusión: **resolver "al vuelo" con
AGE tal como está hoy no elimina la necesidad de precomputar algo tipo closure table** —
en el mejor de los casos, movería el problema de "precomputar CADE completa" a "precomputar
un `propagation_tree`-equivalente dentro del grafo", no lo eliminaría de raíz.

### Caso preocupante: consignado con 2 padres (DAG real) × 90 artículos en una pasada

Archivo: `queries/age_resolve_worst_case.sql`. Escenario: "un cliente pide su lista de
precios completa" — consignado `1001`, que cuelga de DOS cuentas distintas (101 y 102, DAG
real, no árbol), resolviendo su config de entidad + la config de sus 90 artículos en UNA
sola consulta.

**Correctitud**: ✅ SQL (recursive CTE explorando ambas ramas del DAG) y Cypher coincidieron
exactamente: ambas ramas (vía cuenta 101 y vía cuenta 102) convergen en el mismo CUC (id 1)
a la misma profundidad (2) — el DAG se resolvió bien en los dos motores.

**Performance**: SQL **6.0ms** vs Cypher **161ms** (~27x más lento) — en la misma familia
del resultado anterior (33x), no empeoró por agregar el DAG de la entidad.

**El matiz que sí vale la pena remarcar** (contraintuitivo, y por eso valioso): la
preocupación de que "DAG multi-padre × 90 artículos" se multiplicara entre sí **no se
materializó**. Mirando el plan: el traversal del DAG de la entidad se resolvió **una sola
vez** (vía `HashAggregate` antes de cruzar con los artículos), no una vez por cada uno de
los 90 artículos — el costo se mantuvo aditivo (mismo orden de magnitud que resolver un
único artículo), no multiplicativo. Eso sí es un punto a favor de cómo Cypher/AGE estructura
consultas con múltiples `MATCH` + agregación intermedia: cuando la entidad es fija y el
artículo varía, el motor no repite el trabajo de la entidad por cada artículo.

Lo que sigue sin cambiar es el cuello de botella real: el patrón "explora todos los caminos,
filtra después" del traversal de profundidad variable (`*0..N`) — ese, no el DAG en sí, es
el que explica el 27x. El DAG multi-padre resultó ser el actor secundario en este caso, no
el protagonista que parecía a priori.

### Shootout final: ¿quién CONSTRUYE el cierre transitivo más barato? (`queries/closure_build_shootout.sql`)

Pregunta pendiente de la sección anterior: si el problema es que `*0..N` "explora todo y
filtra después" en vez de empujar el filtro hacia dentro, ¿existe una forma de arreglar eso
para construir la closure table de una vez?

**Primer hallazgo: no, no con Cypher puro.** Revisé el catálogo de funciones de AGE
(`\df ag_catalog.*`) buscando `shortestPath()`/BFS/Dijkstra — **no existen en este build**.
Intentar `MATCH p = shortestPath(...)` da `ERROR: syntax error at or near "shortestPath"` —
ni siquiera estaba implementado como para fallar en ejecución. Sin un primitivo de
traversal-con-early-termination, la única forma de construir el cierre transitivo
correctamente (BFS real, cada arista visitada una vez) es la misma en los dos motores:
expansión por niveles controlada desde afuera (un loop), no una sola consulta declarativa.

**La respuesta a "cómo convertir el proceso de varios pasos en una closure table de una
consulta" — sí existe, y es un patrón de SQL, no de grafo**: sembrar la recursión desde
TODOS los nodos con config propia a la vez (no uno por uno) en un solo
`WITH RECURSIVE ... UNION ALL`, dejar que las siembras compitan libremente, y al final
quedarte con la más cercana por nodo vía `DISTINCT ON (id) ORDER BY dist ASC`. Este truco
—sembrar todas las fuentes en paralelo en vez de iterar nodo por nodo— es lo que probablemente
faltaba en los intentos previos con tablas intermedias/hash de estado.

**Comparación final, con el ALGORITMO CORRECTO implementado igual en los dos motores**
(BFS por niveles, cada arista visitada una sola vez — nada de "explorar de más"), para
construir el cierre completo de los 140 `Articulo`:

| Enfoque | Tiempo | Resultado |
|---|---|---|
| SQL — recursive CTE de una sola pasada (multi-fuente + `DISTINCT ON`) | **91.5ms** | ✅ 140/140 |
| SQL — iterativo por niveles (tabla de estado — el patrón real de `fn_propagate_by_depth`) | **51.5ms** | ✅ 140/140 |
| Cypher — declarativo `*0..N` (naive) | **445ms** | ✅ pero de más |
| Cypher — iterativo por niveles (mismo algoritmo que SQL, vía `SET`) | **276ms** | ✅ 140/140 (tras arreglar un bug) |

**Esto contradice la intuición inicial**: aun implementando exactamente el mismo algoritmo
correcto (BFS por niveles) en ambos motores, Cypher fue **3-5x más lento que SQL**, no más
rápido. La hipótesis de que "AGE hace la construcción del grafo más simple y rápida" no se
sostuvo en esta prueba — ni en velocidad ni, honestamente, en simplicidad de código (ver el
bug abajo).

**Bug real encontrado en el camino** (no fue error mío, es de AGE): al correr el `SET`
dinámico vía `EXECUTE` dentro del loop, `GET DIAGNOSTICS ... = ROW_COUNT` **reportó 0 filas
afectadas cuando en realidad sí se habían actualizado 27 nodos** — el loop se detuvo
prematuramente pensando que ya no había nada que resolver, dejando nodos sin resolver
*sin lanzar ningún error*. Tuve que cambiar la condición de paro a "contar nodos resueltos
antes/después" en vez de confiar en `ROW_COUNT`. Esto es más serio que un tema de
performance: es un bug silencioso — si no hubiera verificado el conteo final (52 en vez de
140), el resultado incorrecto habría pasado sin ninguna señal de error.

**Veredicto para la pregunta original**: construir el cierre transitivo con AGE no resultó
"mucho más rápido" — resultó más lento y, en la práctica, con una trampa de confiabilidad
(`ROW_COUNT`) que SQL no tiene. Esto cierra el ciclo completo de este deep dive: ni leer al
vuelo (33x más lento) ni construir el cierre (3-5x más lento) mostraron ventaja de AGE sobre
el enfoque SQL que ya existe en el pipeline real — con los datos que tenemos hoy, a esta
escala, con esta versión de AGE.

## ltree: modelar org/región/GE como jerarquías nativas de Postgres

Archivo: `queries/ltree_poc.sql`. `ltree` **sí está en el allowlist de Cloud SQL y AlloyDB**
(v1.2) — a diferencia de cstore_fdw/Hydra/Citus/AGE, esto es instalable HOY en la
infraestructura real, sin operar Postgres autoadministrado. Es la única opción de toda la
sesión sin ese bloqueador.

**Validación contra el schema real**: `dependant_articles` y `geographic_zones` SÍ son
árboles puros (un padre por fila, confirmado en la migración inicial) — encajan
perfectamente en `ltree`. `entities` (CUC/cuenta/consignado) **sigue siendo un DAG real**
(el mismo hallazgo de toda la sesión) — un nodo no puede tener un solo `ltree` porque no
tiene un solo camino de ancestros. Solución: **materializar múltiples filas por entidad**,
una por cada cadena de ancestros válida (`entity_paths(entity_id, path)`, PK compuesta) — se
probó con el consignado 1001 y generó correctamente sus 2 filas (`e1.e101.e1001` y
`e1.e102.e1001`), y sigue siendo indexable con GiST.

### El hallazgo importante: el diseño de "convergence_path" (concatenar org‖región‖GE en
un solo ltree) de la conversación de referencia **es incorrecto**, no solo para comodines
"a la mitad" — para el caso normal también

Construí una regla que el consignado 1001 debía cumplir sin ambigüedad (CUC 1 + región Norte
+ GE 1 — las 3 dimensiones completas, sin comodines a la mitad). El `convergence_path`
concatenado (`'e1.z1.z11.n1'`) **no matcheó**:

```sql
SELECT 'e1.z1.z11.n1'::ltree @> 'e1.e101.e1001.z1.z11.z111.n1.n101.n1001.n10001'::ltree;
-- f
```

**Por qué**: `@>`/`<@` comparan una sola secuencia de labels, posición por posición. La
regla dice "org=e1" (1 nivel — cualquier profundidad bajo CUC 1), pero el path real del
consignado tiene 3 niveles de org (`e1.e101.e1001`) antes de llegar a la región. En cuanto
`org_scope` es más corto que la profundidad real de la entidad (el caso NORMAL, no el raro
— las reglas generalmente NO se anclan al nodo hoja), la concatenación desalinea las
dimensiones siguientes y el prefix-match falla. Esto no depende de si los comodines son "de
cola" o no — es un problema matemático del operador, no de disciplina de negocio.

**Lo que sí funciona — dos alternativas, verificadas**:

1. **3 columnas `ltree` separadas + 3 predicados `<@`/`@>` con `AND`** (el diseño original,
   antes de la "optimización" del convergence_path) — verificado, correcto:
   ```sql
   WHERE (r.org_scope IS NULL OR r.org_scope @> :entity_path)
     AND (r.region_scope IS NULL OR r.region_scope @> :zone_path)
     AND (r.ge_scope IS NULL OR r.ge_scope @> :article_path)
   ORDER BY (org_scope IS NOT NULL)::int + (region_scope IS NOT NULL)::int + (ge_scope IS NOT NULL)::int DESC
   ```
   Con esto, la regla "CUC 1 + región Norte + GE 1" sí ganó (especificidad 3), por delante de
   las reglas de 1 sola dimensión (especificidad 1) y el default (especificidad 0) — el
   comportamiento correcto.
2. **`lquery` con wildcards de profundidad variable** (`'e1.*.z1.z11.*.n1.*'::lquery`,
   comparado con `~`) — también verificado, correcto (`t`). Matemáticamente es lo que se
   necesitaría para lograr "una sola columna, un solo predicado" de verdad — pero pierde la
   "especificidad gratis vía `nlevel()`" (un patrón con wildcards no tiene una profundidad
   simple que contar) y es más complejo de construir/mantener que las 3 columnas. Por
   `ltree`'s propia documentación, el índice GiST **sí soporta el operador `~`** — no lo
   forzamos aquí porque la tabla es demasiado chica (mismo caso de Seq-Scan-gana-en-tablas-
   chicas visto con AGE), pero es indexable en principio.

**Recomendación**: usar el diseño de 3 columnas + `AND` — es el más simple, es el que
realmente funciona, y la "elegancia" de una sola columna no compensa la complejidad de
`lquery` ni justifica el riesgo de reintroducir el bug de concatenación si alguien la
"optimiza" de vuelta a `convergence_path` sin darse cuenta del problema.

**Conclusión de la sesión completa sobre `ltree`**: a diferencia de columnar (bloqueado por
el allowlist) y AGE (allowlist bloqueado + performance real 3-33x peor que SQL en todo lo que
se probó), `ltree` **sí es instalable y sí modela correctamente 2 de las 3 dimensiones reales
del dominio** (artículo, zona) — la dimensión de entidad requiere el patrón de multi-path
por ser DAG, que ya se validó que funciona. El diseño de reglas de cruce debe usar 3 columnas
separadas, no una columna de convergencia concatenada — esa "optimización" está probada
como incorrecta con datos reales del propio schema.

### Nombrado real y genericidad: columnas nombradas vs. EAV

Columnas mapeadas al dominio real (no a los placeholders "org/región/GE" de la conversación
de referencia): `articulo_scope` (→ `dependant_articles`), `entidad_scope` (→ `entities`,
con el `EXISTS` contra `entity_paths` por el DAG), `zona_scope` (→ `geographic_zones`).
`currency_code`/`operation_type_id` NO son `ltree` — son planos (no hay tabla de jerarquía
para ellos en el schema real), se comparan con `=` normal, no con `@>`.

**¿Generalizan a cualquier escenario futuro?** Depende de qué tan seguido aparezcan
dimensiones jerárquicas NUEVAS (no reglas nuevas — dimensiones nuevas). Dos diseños,
ambos verificados correctos con el mismo caso (consignado 1001, especificidad 3 para la
regla completa):

- **Columnas nombradas** (`articulo_scope`/`entidad_scope`/`zona_scope`): simple, cada
  dimensión con su propio índice GiST. Requiere migración (`ALTER TABLE ADD COLUMN`) si se
  agrega una dimensión jerárquica nueva.
- **EAV genérico** (`regla_scope(regla_id, dimension, scope)`, comodín = ausencia de fila):
  extensible sin DDL, verificado con un `NOT EXISTS` de anti-join (ninguna fila de la regla
  debe fallar su match) — dio el mismo resultado correcto (especificidad 3 para la regla
  completa, 1 para las de una sola dimensión, 0 para el default). Costo: la consulta de
  resolución es más compleja (anti-join en vez de `AND` plano) y el índice GiST queda
  compartido entre dimensiones distintas en la misma columna `scope`.

**Recomendación**: columnas nombradas. Dado el historial de 91 migraciones — la topología
(artículos/entidades/zonas) es estable, lo que cambia constantemente son las reglas SOBRE
ella, no las dimensiones mismas — el costo de una migración ocasional por una dimensión
jerárquica nueva es menor que cargar con la complejidad del anti-join en cada consulta de
resolución (que corre por cada request de precio).
