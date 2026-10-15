# azure-sim — Azure Pipelines + ACR + Azure Artifacts en local

Corre los pipelines **reales** de `templates/gitops/azure-pipelines/` (YAML y `scripts/ci-*`)
sin Azure DevOps: un compilador de templates, un runner de stages/jobs/steps y servicios
reales en Docker. Diseño y límites en
[ADR-0061](../../docs/adr/ADR-0061-azure-pipelines-logica-fuera-del-yaml-y-simulador.md).

## Requisitos: Docker con compose y buildx

Funciona con Docker Desktop (integración WSL activa; así se validó el 2026-10-15) o con
Docker Engine nativo. Recomendado para equipos nuevos: Docker Engine dentro de Ubuntu. Es lo mismo que traen los
agentes `ubuntu-latest`, no requiere licencia de Docker Desktop y se instala igual en
cualquier equipo. Requiere `systemd=true` en `/etc/wsl.conf`.

1. Si Docker Desktop está instalado: Settings → Resources → WSL Integration → apagar la
   distro (si no, vuelve a crear `/usr/bin/docker` como symlink).
2. Instalar (cambia `resolute` por el codename de tu Ubuntu: `. /etc/os-release; echo $VERSION_CODENAME`):

```bash
sudo rm -f /usr/bin/docker   # symlink roto de Docker Desktop, si existe
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu resolute stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # y abrir una terminal nueva
docker run --rm hello-world
```

Si el repo de Docker aún no tiene tu versión de Ubuntu, usa los paquetes de Ubuntu:
`sudo apt-get install -y docker.io docker-buildx docker-compose-v2`.

## Uso

```bash
tools/azure-sim/sim up                 # build del agente + registry, nginx, azure-mock, pypi
tools/azure-sim/sim run all            # los 9 escenarios (estado limpio en cada corrida)
tools/azure-sim/sim run hotfix         # hasta ese escenario (la historia es acumulativa)
tools/azure-sim/sim down               # baja todo y borra .work/
AZURE_SIM=1 python3 -m unittest discover -s tools/tests -p test_azure_sim.py   # e2e incluido
```

Sin Docker, `test_azure_sim.py` corre igual el runner con los YAML y scripts reales.
Solo `az`, `docker`, `uv` y `pip` son fakes.

## Escenarios

| Escenario | Qué demuestra |
|---|---|
| `pr` | Build validation: tests + `docker build` sin push; el ACR queda vacío |
| `merge` | `ctx-<hash>` construido y bloqueado → tag `-dev` automático → release → dev con el mismo digest |
| `rerun-ci` | Commit ajeno no dispara el CI (paths); re-run manual sin rebuild |
| `rc-prod` | rc → qa; prod en HEAD tras commits ajenos → **mismo digest** que el rc, sin build |
| `hotfix` | `hotfix/<svc>/*` → `-hotfix.1` automático → prod, imagen nueva |
| `immutable` | Re-run idempotente; push directo a un tag bloqueado → DENIED; re-apuntar falla |
| `lib` | dev → @Local, rc → Prerelease, prod → Release; re-run → 409 como warning |
| `auth-error` | Error de auth en ACR → falla sin reconstruir ni publicar |
| `paths-drift` | `trigger.paths` sin la lib → el PR falla en `check-paths` |

## Arquitectura

```
host (WSL)                                    Docker
──────────                                    ──────
sim run → azsim (compila YAML, orquesta) ──docker exec──▶ agent (ubuntu:24.04, network host)
            │                                   │  bash scripts/ci-*.sh · az (shim) · docker CLI
            │                                   ▼
            │                          localhost:5000 ─▶ nginx "ACR" ─┬─ GET/blobs ──▶ registry:2
            │                                                          └─ PUT/DELETE manifests ──▶ azure-mock (locks)
            └─ aserciones ─────────────▶ localhost:8081 pypi (feed) · localhost:8082 azure-mock (views, locks)
```

- El daemon de Docker es el del host: el agente monta `/var/run/docker.sock` y el
  directorio de trabajo con la misma ruta absoluta.
- `ACR_LOGIN_SERVER=localhost:5000` y `CI_PKGS_BASE_URL` redirigen los scripts. Son las
  únicas diferencias de configuración respecto a Azure.

## Qué NO valida (solo Azure real)

Expansión real de templates (usa `previewRun: true` de la API de runs), autenticación de
ACR con workload identity ni rol AcrPush, approvals y *Branch control* de Environments
(aquí se auto-aprueban), varios tags en un solo push, `paths` en pushes de tags, `batch`,
la normalización real del nombre de paquete en la API de views y las tasks reales
(`AzureCLI@2` hace login; `TwineAuthenticate@1` escribe un `.pypirc` equivalente).

Lo que el simulador no conoce **falla explícitamente**: funciones de expresión, tasks,
`extends`, templates de otro repo y lifecycle hooks distintos de `deploy`.
