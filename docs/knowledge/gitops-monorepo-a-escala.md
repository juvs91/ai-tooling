# Un repo, muchos equipos: el monorepo de Google y el nuestro

Google pone casi todo su código en un solo repositorio para que miles de ingenieros compartan
librerías, revisen el código de otros y hagan cambios que cruzan equipos en un solo paso. Este
documento explica cómo lo resolvieron y cómo nuestro GitOps monorepo en Azure DevOps aplica
las mismas ideas a una escala menor.

Referencias internas: ADR-0007 (modelo), ADR-0060/0061 (Azure Pipelines), ADR-0080 (lector
de dependencias) y ADR-0081 (migración sin romper).

---

## 1. Cómo lo resolvió Google

Cifras de 2015 (Potvin y Levenberg, *Communications of the ACM*, 2016):

| Dato | Valor |
|---|---|
| Ingenieros en un solo repo | ~25,000 |
| Archivos fuente | ~9 millones |
| Líneas de código | ~2,000 millones |
| Tamaño del repo | 86 TB |
| Cambios por día | ~16,000 manuales + ~24,000 automatizados |

Tres piezas principales:

- **Piper: dónde vive el código.** Ningún sistema comercial ni open source aguantaba un repo de
  ese tamaño, así que Google construyó el suyo. Es centralizado, corre sobre Bigtable (luego
  Spanner) y está replicado en varios data centers. Todos trabajan sobre trunk; las ramas casi
  solo se usan para releases.
- **CitC (Clients in the Cloud): cómo lo ves.** Nadie clona el repo. Cada ingeniero ve el código
  completo como un disco local, pero solo se guardan sus cambios. Puede abrir cualquier archivo
  de cualquier equipo sin bajar 86 TB.
- **Borg: cómo corre.** Es el sistema que ejecuta los servicios en producción, con
  especificaciones declarativas, en clusters de decenas de miles de máquinas. Kubernetes nació
  de las lecciones de Borg.

Alrededor de esas tres piezas hay más herramientas:

- **Blaze** (hoy Bazel) conoce el grafo de dependencias.
- **TAP** corre antes del merge solo las pruebas afectadas por cada cambio.
- Los archivos **OWNERS** dicen quién aprueba cada carpeta.
- **Rosie** parte refactors gigantes en miles de cambios pequeños.

---

## 2. Nuestro modelo

La regla central es **`tag = versión en producción por proyecto`**. Los ambientes no son
ramas: DEV, QA y PROD salen de qué tag disparó el pipeline. Cada equipo baja solo sus carpetas,
todos integran a `main` y cada servicio libera a su ritmo.

```
  Equipo A            Equipo B            Equipo C            Platform
  ventas/api          compras/api         rh/api              _shared/libs/*
  (en disco: su       (en disco: su       (en disco: su       (en disco: solo
   carpeta + auth)     carpeta + logging)  carpeta + auth)     la lib que toca)
      │                   │                   │                   │
      └──── PR a main ────┴── revisores por ruta + build validation ──┘
                                │
  ┌─────────────────────────────▼──────────────────────────────────────┐
  │  Monorepo en Azure Repos · rama main · un solo historial          │
  │  ventas/api  compras/api  rh/api  _shared/libs/auth  .../logging  │
  └─────────────────────────────┬──────────────────────────────────────┘
                                │
  ┌─────────────────────────────▼──────────────────────────────────────┐
  │  Azure Pipelines por servicio                                      │
  │  cambió ventas/api            → build + test solo de ventas/api    │
  │  cambió _shared/libs/auth     → solo los servicios que la declaran │
  └─────────────────────────────┬──────────────────────────────────────┘
                                │
                DEV (auto)              QA (approval)          PROD (approval)
  ventas-api    ventas-api@1.4.2-dev.1 → ventas-api@1.4.2-rc.1 → ventas-api@1.4.2
  compras-api   compras-api@2.1.0-dev.3   (sin release)          compras-api@2.0.0
                                                                 (no se mueve)
```

La imagen se identifica por su **contenido** (los tree hashes de git del servicio y de sus
librerías). PROD recibe el mismo digest que aprobó QA; solo cambia el tag.

Ramas permitidas:

| Tipo | Patrón | Vida máxima | Origen |
|---|---|---|---|
| Trunk | `main` | Permanente | — |
| Feature | `feature/*` | 3 días | trunk |
| Integración | `integration/*` | 2 semanas | trunk |
| Hotfix | `hotfix/<servicio>/<nombre>` | Días | tag de prod |

---

## 3. Lo que hace Google y lo que tenemos nosotros

| Capa | Google | Nosotros | Qué tan cerca |
|---|---|---|---|
| Repositorio | Piper: centralizado, replicado, hecho a la medida | Un monorepo Git en Azure Repos | Misma idea |
| Vista del dev | CitC: ves todo sin descargarlo | Partial clone + sparse checkout en modo cone + `release.sh work/add/drop` | Parecido |
| Ramas | Trunk-based; ramas solo para release | Trunk-based con ramas cortas y hotfix desde el tag | Misma idea |
| Propiedad | Archivos OWNERS | Policy "Automatically included reviewers" con filtro de ruta (Azure Repos no lee CODEOWNERS) | Parecido |
| Pruebas | TAP antes del merge, con el grafo de Blaze | Build validation en cada PR, por servicio; las libs salen del manifiesto | Parecido |
| Releases | Ramas de release automatizadas | Tags `servicio@semver`, imagen por contenido, mismo digest de QA a PROD | Misma idea |
| Ejecución | Borg | Imágenes en ACR y Environments de Azure DevOps con approvals | Parcial |

---

## 4. Analogía: una planta con una sola bóveda de planos

Piensa en una planta que arma varios programas de chasis. Si cada línea guarda su propia copia
de los planos, tarde o temprano dos líneas fabrican el mismo soporte con revisiones distintas.
Un monorepo equivale a una sola bóveda de planos para toda la planta.

| En la planta | En el código |
|---|---|
| Bóveda central de planos (PDM) con historial | Piper / nuestro monorepo: una sola fuente de verdad |
| Estación que consulta cualquier plano sin imprimir la bóveda | CitC / sparse checkout: en disco solo lo tuyo |
| Componentes comunes (tornillería, soportes) | `_shared/libs/`: librerías que usan dos o más servicios |
| Cambio de ingeniería con "where-used" | `release.sh consumers` + CI de los servicios que usan la lib |
| Firma del ingeniero responsable por número de parte | OWNERS / revisores por ruta |
| La línea corre la Rev C mientras ingeniería trabaja la Rev D | Tag `ventas-api@1.4.2` en prod mientras `main` avanza |
| Desviación temporal sobre la revisión liberada | Hotfix desde el tag + `release.sh cherry` a `main` |
| Programación de la línea | Borg / Kubernetes / Environments |

---

## 5. Bajar solo lo tuyo: clone, sparse checkout y LFS

Git no tiene un sistema de archivos virtual como CitC, pero tres opciones juntas se acercan.
Cada una recorta una parte distinta:

| Herramienta | Qué recorta | Pregunta que responde |
|---|---|---|
| `clone --filter=blob:none` | El contenido de los archivos en la historia | ¿Qué bajo de la red? |
| `sparse-checkout` (cone) | Los archivos que aparecen en tu carpeta | ¿Qué veo en disco? |
| Git LFS | Los binarios pesados | ¿Dónde viven los archivos grandes? |

### Tipos de clone

| Comando | Historia | Contenido de archivos | En disco |
|---|---|---|---|
| `git clone` | Completa | Todas las versiones de todo | Todo |
| `--depth 1` (shallow) | Solo el último commit | Solo del último commit | Todo |
| `--filter=blob:none` (partial) | Completa | Bajo demanda | Todo |
| `--filter=blob:none --sparse` | Completa | Solo el de tus carpetas | Raíz, hasta elegir carpetas |

- **Shallow corta la historia.** Por eso nuestros pipelines hacen checkout con `fetchDepth: 0`
  y `fetchTags: true`: los pipelines nuevos de Azure clonan con profundidad 1 y sin tags.
- **Partial clone conserva la historia.** `git log` y `git tag` funcionan completos, y el
  contenido de un archivo se baja la primera vez que Git lo necesita.
- **Azure Repos acepta clones parciales** (`blob:none` y `tree:0`) desde noviembre de 2023. En
  Azure DevOps Server hay que confirmarlo según la versión. Si el servidor no soporta el
  filtro, Git avisa y hace un clone completo.

### Sparse checkout en modo cone

Le das carpetas a Git. Por cada carpeta, escribe en disco:

1. Todo lo que hay debajo de ella, a cualquier profundidad.
2. Los archivos sueltos de cada carpeta padre, pero no sus subcarpetas.
3. Los archivos de la raíz del repo, siempre.

```
# ./scripts/release.sh work ventas/api
repo/
├── README.md, .gitattributes         ✓ raíz, siempre
├── ventas/
│   ├── (archivos sueltos en ventas/) ✓ carpeta padre
│   ├── api/**                        ✓ todo, recursivo
│   └── web/                          ✗ no existe en disco
├── compras/                          ✗
├── _shared/libs/
│   ├── auth/**                       ✓ declarada en ventas/api/pyproject.toml
│   └── logging/                      ✗
└── scripts/**                        ✓
```

- **Es más rápido que usar patrones.** Sin cone, Git compara cada patrón contra cada archivo
  del índice. Con cone usa búsquedas por hash sobre carpetas.
- **Es el default de `git sparse-checkout set`** desde hace varias versiones.
- **`--sparse-index` es el siguiente paso.** Reduce el índice a una entrada por carpeta fuera
  de tu selección y acelera `status`, `add` y `commit`. Es opcional y experimental, y algunas
  herramientas externas no lo entienden. Conviene cuando `git status` se sienta lento.

Comandos útiles: `git sparse-checkout check-rules` (¿esta ruta entra en mi selección?) y
`git sparse-checkout clean --dry-run` (qué sobra fuera de la selección).

### Git LFS para binarios pesados

Con LFS, el commit guarda un puntero de ~130 bytes y el binario vive en un servidor aparte. Al
hacer checkout, Git cambia el puntero por el binario, solo para la versión que usas. Azure
Repos lo soporta.

```gitattributes
# .gitattributes
*.onnx     filter=lfs diff=lfs merge=lfs -text   # modelos de varios MB → LFS
*.parquet  filter=lfs diff=lfs merge=lfs -text   # datasets → LFS
*.png      binary                                # chicos: solo binario, sin LFS
```

Vale la pena cuando los archivos pesan varios MB y cambian seguido. Para archivos de pocos KB
basta marcarlos como `binary`.

### Cómo se empieza

```bash
git clone --filter=blob:none --sparse https://dev.azure.com/<org>/<proyecto>/_git/<repo>
cd <repo>
export GITOPS_PROJECTS_DIR=. GITOPS_SHARED_DIR=_shared/libs   # layout de Azure
./scripts/release.sh work ventas/api      # tu servicio + sus libs + scripts
./scripts/release.sh add compras/api      # otra carpeta cuando la necesites
```

**La diferencia con CitC:** en CitC puedes abrir cualquier archivo aunque no lo hayas pedido.
En Git, lo que está fuera de tu selección no existe en disco hasta que lo agregas. Aun así,
`git log`, `git diff` y `git tag` ven el monorepo completo.

**Sparse checkout no evita los choques ni controla el acceso.** Dos personas pueden editar el
mismo archivo de una librería compartida aunque cada una tenga su propia vista, y cualquiera
puede agregar cualquier carpeta. Lo que evita que los equipos se pisen es el resto del modelo:
revisores por ruta, ramas cortas, tags por servicio y build validation en cada PR.

---

## 6. Cómo se declara que un servicio usa una librería

Las librerías se consumen **desde el código del monorepo**, no como versiones publicadas. La
dependencia se declara **una sola vez**, por ruta, en el manifiesto que el lenguaje ya
necesita (ADR-0080):

| Manifiesto | Declaración |
|---|---|
| `pyproject.toml` | `[tool.uv.sources] metalsa-auth = { path = "../../_shared/libs/auth" }` o `{ workspace = true }` |
| `package.json` | `"@metalsa/ui": "file:../../_shared/libs/ui"` o `"workspace:*"` |
| `go.mod` | `replace example.com/auth => ../../_shared/libs/auth` |
| `deps.txt` | Una ruta por línea, solo para carpetas sin manifiesto (Terraform, solo Dockerfile) |

`scripts/deps.py` lee esos manifiestos desde git, sigue la cadena (si una librería usa otra,
también cuenta) y alimenta todo lo demás:

```bash
python3 scripts/deps.py paths ventas/api            # servicio + libs: sparse checkout y hash de imagen
python3 scripts/deps.py consumers _shared/libs/auth # where-used
python3 scripts/deps.py trigger-block ventas/api    # bloque trigger.paths para el YAML
python3 scripts/deps.py policy-filter ventas/api    # filtro de la policy de build validation
python3 scripts/deps.py audit                       # libs pedidas por nombre sin ruta declarada
./scripts/release.sh consumers _shared/libs/auth    # where-used, con el comando work sugerido
```

**Si un servicio pide la librería solo por nombre** (por ejemplo `metalsa-auth>=0.1` sin
`[tool.uv.sources]`), se detecta igual y aparece un aviso con el bloque que falta.
`python3 scripts/deps.py audit` lista todos esos casos; cuando quede limpio, la variable
`DEPS_STRICT=1` en el pipeline vuelve error cualquier caso nuevo.

`deps.py` necesita Python 3.11 o mayor. `release.sh` lo busca solo: prueba `python3.11` y
posteriores, y si no hay, usa `uv` (que descarga uno). Los agentes de Azure ya traen 3.12.

Azure no acepta parámetros en los triggers, así que `trigger.paths.include` sigue en el YAML.
Pero ya no se escribe a mano: se genera con `trigger-block`, y el step "Validar trigger.paths"
falla en el PR si se queda atrás.

**Por qué no publicar primero cada librería.** Publicarla en Azure Artifacts y fijar versión en
cada servicio quitaría las listas del YAML, pero perderíamos lo que hace valioso al monorepo:

- **El cambio atómico.** No podrías cambiar una API y arreglar a todos sus usuarios en el mismo
  PR.
- **Una sola versión.** Unos servicios se quedarían en la v1 y otros en la v2.
- **Los errores salen más tarde.** Una ruptura aparecería cuando el consumidor sube de versión,
  no en el PR de la librería.

Publicar sigue sirviendo para quien está fuera del monorepo.

---

## 7. Beneficios y contras

### Cómo se benefician los equipos entre sí

- **Ven y reusan el código de otros.** Encuentras la API que ya existe y ejemplos reales de uso.
  En la encuesta de Jaspan et al. (2018), la visibilidad fue la razón principal por la que los
  ingenieros de Google prefieren el monorepo.
- **Una sola versión de cada librería.** Fue la segunda razón más valorada: gestión de
  dependencias centralizada.
- **Quien cambia algo compartido arregla a sus usuarios** en el mismo PR.
- **El pipeline coordina, no las juntas.** Cuando Platform toca una librería, el build
  validation corre en cada servicio que la declara.
- **Cada servicio libera a su ritmo** gracias a los tags por servicio.
- **Mismas herramientas para todos:** `release.sh`, los templates de Azure Pipelines y el
  mismo flujo de ramas.

### Contras y cómo los mitigamos

| Contra | Mitigación |
|---|---|
| Las herramientas cuestan. Google construyó Piper, CitC, TAP y Blaze | Git y Azure DevOps aguantan nuestro tamaño; la lógica propia (sparse, tags, `deps.py`) está en scripts con tests y en un simulador local |
| Dependencias que nadie planeó: con todo a la vista es fácil usar código interno de otro equipo | Revisores por ruta obligatorios; la dependencia tiene que declararse en el manifiesto para que el CI y la imagen la incluyan |
| Menos libertad de herramientas y de versiones que en un multirepo | Decisión consciente: una versión desde el monorepo. Fijar versión solo en casos puntuales y a propósito |
| No había "where-used" | `release.sh consumers <lib>` / `deps.py consumers` |
| La lista de librerías vivía a mano en varios lugares | El manifiesto es la fuente única. `trigger.paths` y el filtro de la policy se generan y se validan en cada PR |
| Servicios que piden la librería solo por nombre, sin ruta | Se detectan igual, con aviso y el bloque que falta. `deps.py audit` los lista; `DEPS_STRICT=1` cierra la puerta cuando todos migren |
| `deps.py` necesita Python 3.11 y el de macOS es 3.9 | `release.sh` busca `python3.11+` y, si no hay, usa `uv`, que descarga uno |
| Otros repos con el mismo `release.sh` (`commons`) | Pendiente: copiar `deps.py` con `install-hooks.sh` y portar los cambios de `release.sh` |
| Reglas que dependen de disciplina (vida de ramas, limpieza de tags `-dev`) | Pendiente: un job que avise de ramas viejas y limpie tags de más de 30 días (las imágenes ya se purgan con `acr purge`) |
| Parte del flujo no se ha probado en Azure real (tags con `@`, varios tags por push, *Branch control* con tags) | El simulador local cubre los scripts; falta correrlo en un pipeline sandbox |
| Curva de aprendizaje: sparse checkout, trunk-based y tags por servicio | Este documento, la skill `gitops-monorepo` y `release.sh help` |

### Qué copiar de Google cuando entren más equipos

- **Un solo pipeline que calcule los servicios afectados** y los construya con una matriz
  dinámica, como TAP. El grafo ya existe en `deps.py`.
- **Revisores obligatorios por ruta** en `main` para cada carpeta, con Platform en lo compartido.
- **`--sparse-index`** cuando `git status` se sienta lento.

---

## Fuentes

- Potvin y Levenberg, "Why Google Stores Billions of Lines of Code in a Single Repository",
  *Communications of the ACM* 59(7), 2016. <https://research.google/pubs/pub45424/>
- Jaspan et al., "Advantages and Disadvantages of a Monolithic Repository: A Case Study at
  Google", ICSE-SEIP 2018. <https://people.engr.ncsu.edu/ermurph3/papers/seip18.pdf>
- Verma et al., "Large-scale cluster management at Google with Borg", EuroSys 2015.
  <https://research.google/pubs/pub43438/>
- Winters, Manshreck y Wright, *Software Engineering at Google*, O'Reilly 2020, caps. 16, 18 y 23.
  <https://abseil.io/resources/swe-book>
- Documentación de Git, `git-sparse-checkout`. <https://git-scm.com/docs/git-sparse-checkout>
- Microsoft, "Git partial clone now supported in Azure DevOps", 2023.
  <https://devblogs.microsoft.com/devops/git-partial-clone-now-supported-in-azure-devops/>
- En este repo: ADR-0007, ADR-0060, ADR-0061, ADR-0080, ADR-0081, `templates/gitops/azure-pipelines/README.md`
  y la skill `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`.
