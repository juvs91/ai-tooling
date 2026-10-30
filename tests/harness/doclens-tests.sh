#!/usr/bin/env bash
# tests/harness/doclens-tests.sh — doclens homologado (ADR-0076): espejo, migración de la
# estructura vieja, venv aislado y listo para usar, doc-guard con arranque perezoso.
# Sin red: python y uv son stubs (el uv falso registra args y entorno con que lo llaman).
# Uso: bash tests/harness/doclens-tests.sh   (también lo invoca run-tests.sh)
set -uo pipefail

AI_TOOLING="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
H="$AI_TOOLING/.claude/hooks"
T=$(mktemp -d); T=$(cd -P "$T" && pwd -P)
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1"; }
eq()  { [ "$2" = "$3" ] && ok "$1" || bad "$1 (got [$2] want [$3])"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 (no contiene [$3]: ${2%%$'\n'*})" ;; esac; }
unset GIT_DIR GIT_INDEX_FILE CLAUDE_PROJECT_DIR DOCLENS_PYTHON VIRTUAL_ENV CONDA_PREFIX
g() { git -c user.name=t -c user.email=t@t -c commit.gpgsign=false "$@"; }

# ── stubs ─────────────────────────────────────────────────────────────────────
export STUB="$T/stub-python" UVLOG="$T/uv.log"
cat > "$STUB" <<'EOF'
#!/bin/sh
# python falso: import-check (falla si el venv tiene BROKEN), smoke, -m, o "procesar" un archivo
case "$1" in
  -c) case "$2" in *"import fitz"*) [ -f "$(dirname "$0")/../BROKEN" ] && exit 1 ;; esac; exit 0 ;;
  -)  cat >/dev/null; echo "doclens: smoke OK — stub"; exit 0 ;;
  -m) exit 0 ;;
esac
d="$2.inspect"; mkdir -p "$d"; : > "$d/index.md"; : > "$d/downscaled.png"; echo "INSPECT_DIR=$d"
EOF
mkdir -p "$T/fakeuv"
cat > "$T/fakeuv/uv" <<'EOF'
#!/bin/sh
echo "uv $* | VIRTUAL_ENV=${VIRTUAL_ENV:-} CONDA_PREFIX=${CONDA_PREFIX:-} UV_PYTHON_DOWNLOADS=${UV_PYTHON_DOWNLOADS:-}" >> "$UVLOG"
if [ "$1" = venv ]; then for a; do v="$a"; done; mkdir -p "$v/bin"; cp "$STUB" "$v/bin/python"; chmod +x "$v/bin/python"; fi
exit 0
EOF
chmod +x "$STUB" "$T/fakeuv/uv"
# python3 falso en el PATH: si /usr/bin/python3 es < 3.9 (p. ej. Alpine 3.10 → 3.7) sync.sh
# pasa al python3 del PATH — los tests no dependen del Python del sistema
ln -s "$STUB" "$T/fakeuv/python3"
UVPATH="$T/fakeuv:$PATH"

# ── toolbox de prueba: repo git con doclens/ (lo versionado + lo nuevo sin ignorar) ──
TB="$T/tb"; mkdir -p "$TB"
(cd "$AI_TOOLING" && git ls-files --cached --others --exclude-standard -- doclens) | while IFS= read -r f; do
  [ -f "$AI_TOOLING/$f" ] && mkdir -p "$TB/$(dirname "$f")" && cp -p "$AI_TOOLING/$f" "$TB/$f"
done
(cd "$TB" && g init -q && g add -A && g commit -qm tb) >/dev/null
SYNC="$TB/doclens/sync.sh"
SRCLIST() { (cd "$TB/doclens" && git ls-files | LC_ALL=C sort); }

consumer() {  # repo git nuevo; imprime su path (6 X: busybox)
  local c; c=$(mktemp -d "$T/c.XXXXXX") || { echo "mktemp falló" >&2; exit 1; }
  (cd "$c" && g init -q && mkdir .claude && touch README && g add -A && g commit -qm i) >/dev/null
  echo "$c"
}
stubvenv() { mkdir -p "$1/bin"; cp "$STUB" "$1/bin/python"; chmod +x "$1/bin/python"; : > "$1/MARK"; }
mirror_ok() {  # doclens/ del consumer == lo versionado en el toolbox (lista + contenido)
  local src f; src=$(SRCLIST)
  [ "$(cd "$1/doclens" && find . -type f ! -path './.venv*' | sed 's|^\./||' | LC_ALL=C sort)" = "$src" ] || return 1
  while IFS= read -r f; do cmp -s "$TB/doclens/$f" "$1/doclens/$f" || return 1; done <<< "$src"
}
dg() {  # dg <root> <file> [VAR=valor…] → "rc|stderr" de doc-guard
  local root="$1" file="$2" e rc; shift 2
  e=$(echo "{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$file\"}}" \
      | (cd "$root" && env CLAUDE_PROJECT_DIR="$root" PATH="$UVPATH" "$@" bash "$H/doc-guard.sh" 2>&1 >/dev/null)); rc=$?
  echo "$rc|$e"
}

echo "══ D1: consumer nuevo → espejo exacto, venv propio preservado ══"
C1=$(consumer); stubvenv "$C1/doclens/.venv"
has "--check antes → update disponible" "$(bash "$SYNC" "$C1" --check)" "update disponible"
OUT=$(bash "$SYNC" "$C1" 2>&1); RC=$?
eq "sync → exit 0" "$RC" 0
has "reporta sincronizado" "$OUT" "doclens sincronizado"
mirror_ok "$C1" && ok "doclens/ idéntico a lo versionado en el toolbox" || bad "espejo distinto"
[ -f "$C1/doclens/.venv/MARK" ] && ok "doclens/.venv existente intacto" || bad "venv perdido"
[ -f "$C1/doclens/.gitignore" ] && grep -qx '.venv/' "$C1/doclens/.gitignore" && ok "doclens/.gitignore ignora .venv/" || bad "sin doclens/.gitignore"
(cd "$C1" && g add -A >/dev/null && [ -z "$(git ls-files doclens/.venv)" ]) && ok "git no versiona doclens/.venv" || bad ".venv entró a git"
has "--check después → al día" "$(bash "$SYNC" "$C1" --check)" "doclens al día"

echo "══ D2: segunda corrida — no-op ══"
REF="$T/d2ref"; sleep 1; : > "$REF"; sleep 1
OUT=$(bash "$SYNC" "$C1" 2>&1)
has "reporta al día" "$OUT" "doclens al día"
[ -z "$(find "$C1/doclens" -newer "$REF" ! -path '*/.venv*' -type f)" ] && ok "ningún archivo reescrito" || bad "reescribió archivos sin cambios"
[ -z "$(ls -d "$C1"/.claude/.harness-backup-*-doclens 2>/dev/null)" ] && ok "sin respaldo" || bad "respaldo sin cambios"

echo "══ D3: cambio en un adapter SIN subir VERSION → se propaga ══"
echo "// cambio" >> "$TB/doclens/adapters/opencode/doclens-tool.ts"; (cd "$TB" && g commit -qam adapter)
bash "$SYNC" "$C1" >/dev/null 2>&1
cmp -s "$TB/doclens/adapters/opencode/doclens-tool.ts" "$C1/doclens/adapters/opencode/doclens-tool.ts" && ok "adapter propagado (contenido, no VERSION)" || bad "adapter no propagado"

echo "══ D4: archivo que sobra en el consumer → se borra con respaldo ══"
echo local > "$C1/doclens/extra.txt"
bash "$SYNC" "$C1" >/dev/null 2>&1
[ ! -f "$C1/doclens/extra.txt" ] && ok "extra.txt eliminado" || bad "extra.txt sigue"
ls "$C1"/.claude/.harness-backup-*-doclens/doclens/extra.txt >/dev/null 2>&1 && ok "extra.txt en el respaldo" || bad "sin respaldo de extra.txt"
mirror_ok "$C1" && ok "espejo exacto tras limpiar" || bad "espejo distinto tras limpiar"

echo "══ D5: estructura vieja (scripts/) → migrada con respaldo, venv movido, aviso de rutas viejas ══"
C2=$(consumer); mkdir -p "$C2/scripts" "$C2/.opencode/tools" "$C2/.claude/hooks"
printf 'DOCLENS_VERSION = "1.0.0"\n' > "$C2/scripts/doclens.py"; echo pymupdf > "$C2/scripts/requirements-doc-tools.txt"; echo 1.0.0 > "$C2/scripts/.doclens-version"
stubvenv "$C2/scripts/.venv-doc-tools"
echo 'const s = "scripts/doclens.py"' > "$C2/.opencode/tools/doclens.ts"
cp "$H/doc-guard.sh" "$C2/.claude/hooks/doc-guard.sh"   # menciona scripts/ como transición: no es aviso
(cd "$C2" && g add -A && g commit -qm legacy) >/dev/null
has "--check → estructura vieja" "$(bash "$SYNC" "$C2" --check)" "estructura vieja"
OUT=$(bash "$SYNC" "$C2" 2>&1); RC=$?
eq "sync → exit 0" "$RC" 0
for f in doclens.py requirements-doc-tools.txt .doclens-version; do
  [ ! -e "$C2/scripts/$f" ] && ls "$C2"/.claude/.harness-backup-*-doclens/scripts/$f >/dev/null 2>&1 \
    && ok "scripts/$f respaldado y quitado" || bad "scripts/$f no migrado"
done
[ -f "$C2/doclens/.venv/MARK" ] && [ ! -e "$C2/scripts/.venv-doc-tools" ] && ok "venv movido (sin reinstalar)" || bad "venv no movido"
mirror_ok "$C2" && ok "doclens/ espejo exacto" || bad "espejo distinto"
has "aviso: .opencode/tools/doclens.ts usa la ruta vieja" "$OUT" ".opencode/tools/doclens.ts"
case "$OUT" in *"hooks/doc-guard.sh"*) bad "doc-guard.sh reportado como ruta vieja" ;; *) ok "doc-guard.sh (transición) no se reporta" ;; esac
has "--check después → al día" "$(bash "$SYNC" "$C2" --check)" "doclens al día"

echo "══ D6: scripts/doclens.py AJENO → intacto ══"
C3=$(consumer); mkdir -p "$C3/scripts"; echo "print('mío')" > "$C3/scripts/doclens.py"; stubvenv "$C3/doclens/.venv"
case "$(bash "$SYNC" "$C3" --check)" in "estructura vieja"*) bad "ajeno tomado como estructura vieja" ;; *) ok "--check no lo toma como nuestro" ;; esac
bash "$SYNC" "$C3" >/dev/null 2>&1
[ "$(cat "$C3/scripts/doclens.py")" = "print('mío')" ] && ok "scripts/doclens.py ajeno intacto" || bad "pisó un archivo ajeno"

echo "══ D7: el propio toolbox → no copia, solo venv ══"
stubvenv "$TB/doclens/.venv"
eq "--check → toolbox" "$(bash "$SYNC" "$TB" --check)" "toolbox: solo venv"
OUT=$(bash "$SYNC" "$TB" 2>&1); RC=$?
eq "sync en el toolbox → exit 0" "$RC" 0
case "$OUT" in *sincronizado*) bad "el toolbox se copió a sí mismo" ;; *) ok "no se copia a sí mismo" ;; esac
eq "copia espejo del consumer → también modo propio" "$(cd "$C1/doclens/adapters" && bash ../sync.sh --check 2>&1)" "toolbox: solo venv"

echo "══ D8: venv nuevo aislado del Python del proyecto ══"
C4=$(consumer); bash "$SYNC" "$C4" --check >/dev/null
mkdir -p "$C4/.venv/lib/site-packages"; printf 'home = /x\n' > "$C4/.venv/pyvenv.cfg"; echo pkg > "$C4/.venv/lib/site-packages/p"
echo "9.99.9" > "$C4/.python-version"
CK0=$(cd "$C4/.venv" && find . -type f -exec cksum {} + | sort)
: > "$UVLOG"
OUT=$(cd "$C4" && env PATH="$UVPATH" VIRTUAL_ENV="$C4/.venv" CONDA_PREFIX=/opt/conda bash "$SYNC" "$C4" 2>&1); RC=$?
eq "sync con venv del proyecto activado → exit 0" "$RC" 0
[ -x "$C4/doclens/.venv/bin/python" ] && ok "doclens/.venv creado" || bad "no creó doclens/.venv"
grep -q '^uv venv .*--python /' "$UVLOG" && ok "uv venv con --python absoluto" || bad "uv venv sin python explícito: $(head -1 "$UVLOG")"
grep -q 'VIRTUAL_ENV= CONDA_PREFIX= UV_PYTHON_DOWNLOADS=never' "$UVLOG" && ok "sin VIRTUAL_ENV/CONDA_PREFIX heredados y sin descargas de Python" || bad "entorno filtrado: $(head -1 "$UVLOG")"
eq "venv del proyecto intacto" "$(cd "$C4/.venv" && find . -type f -exec cksum {} + | sort)" "$CK0"
has "smoke ejecutado" "$OUT" "smoke OK"

echo "══ D9: venv roto → se recrea ══"
: > "$C4/doclens/.venv/BROKEN"; : > "$UVLOG"
(cd "$C4" && env PATH="$UVPATH" bash "$SYNC" "$C4" >/dev/null 2>&1)
[ ! -f "$C4/doclens/.venv/BROKEN" ] && grep -q '^uv venv' "$UVLOG" && ok "venv roto recreado" || bad "venv roto no recreado"

echo "══ D10: lock compartido y reintento tras fallo ══"
C5=$(consumer); cp -R "$TB/doclens" "$C5/doclens"; rm -rf "$C5/doclens/.venv"
mkdir "$C5/doclens/.venv.lock"
OUT=$(env PATH="$UVPATH" bash "$C5/doclens/sync.sh" "$C5" --venv 2>&1); RC=$?
eq "lock fresco → exit 3 (otro proceso)" "$RC" 3
touch -t 202001010000 "$C5/doclens/.venv.lock"
env PATH="$UVPATH" bash "$C5/doclens/sync.sh" "$C5" --venv >/dev/null 2>&1; RC=$?
eq "lock huérfano (>10 min) → se libera y crea" "$RC" 0
[ ! -d "$C5/doclens/.venv.lock" ] && ok "lock liberado al terminar" || bad "lock quedó tomado"
rm -rf "$C5/doclens/.venv"; date > "$C5/doclens/.venv.failed"
OUT=$(env PATH="$UVPATH" bash "$C5/doclens/sync.sh" "$C5" --venv 2>&1); RC=$?
eq "falló hace < 1 h → no reintenta (exit 1)" "$RC" 1
has "explica cómo forzar" "$OUT" "--force"
env PATH="$UVPATH" bash "$C5/doclens/sync.sh" "$C5" --force >/dev/null 2>&1; RC=$?
eq "--force reintenta y crea" "$RC" 0
[ ! -f "$C5/doclens/.venv.failed" ] && ok "éxito borra .venv.failed" || bad ".venv.failed quedó"

echo "══ D11: doc-guard listo para usar — arranque perezoso, lock, fallo, DOCLENS_PYTHON ══"
: > "$T/doc.pdf"
C6=$(consumer); cp -R "$TB/doclens" "$C6/doclens"; rm -rf "$C6/doclens/.venv"
R=$(dg "$C6" "$T/doc.pdf"); case "$R" in "2|"*pre-procesado*) ok "sin venv: lo crea solo y procesa el PDF" ;; *) bad "arranque perezoso: ${R%%$'\n'*}" ;; esac
[ -x "$C6/doclens/.venv/bin/python" ] && ok "doclens/.venv creado por doc-guard" || bad "doc-guard no creó el venv"
rm -rf "$C6/doclens/.venv" "$T/doc.pdf.inspect"; mkdir "$C6/doclens/.venv.lock"
R=$(dg "$C6" "$T/doc.pdf"); case "$R" in "2|"*"reintenta el Read"*) ok "lock tomado → 'reintenta' (no 2º arranque)" ;; *) bad "con lock: ${R%%$'\n'*}" ;; esac
rmdir "$C6/doclens/.venv.lock"; date > "$C6/doclens/.venv.failed"
R=$(dg "$C6" "$T/doc.pdf"); case "$R" in "2|"*"bash doclens/sync.sh --force"*) ok "fallo reciente → mensaje manual con el comando" ;; *) bad "fallo reciente: ${R%%$'\n'*}" ;; esac
rm -f "$C6/doclens/.venv.failed"
R=$(dg "$C6" "$T/doc.pdf" DOCLENS_PYTHON="$STUB"); case "$R" in "2|"*pre-procesado*) ok "DOCLENS_PYTHON → usa ese intérprete" ;; *) bad "DOCLENS_PYTHON: ${R%%$'\n'*}" ;; esac
[ ! -e "$C6/doclens/.venv" ] && ok "con DOCLENS_PYTHON no crea doclens/.venv" || bad "creó venv pese a DOCLENS_PYTHON"
cmp -s "$H/doc-guard.sh" "$AI_TOOLING/doclens/adapters/claude-code/doc-guard.sh" && ok "doc-guard.sh == adapter de doclens" || bad "las 2 copias de doc-guard.sh difieren"

echo "══ D12: auto-sync real en un consumer viejo → migrado y el doctor sin avisos ══"
C7=$(consumer); mkdir -p "$C7/scripts"
printf 'DOCLENS_VERSION = "1.0.0"\n' > "$C7/scripts/doclens.py"; echo 1.0.0 > "$C7/scripts/.doclens-version"
stubvenv "$C7/scripts/.venv-doc-tools"
echo "{\"local_path\":\"$AI_TOOLING\",\"sync_skills\":false,\"sync_hooks\":false}" > "$C7/.ai-tooling"
echo "{\"cwd\":\"$C7\"}" | (cd "$C7" && env CLAUDE_PROJECT_DIR="$C7" PATH="$UVPATH" bash "$H/auto-sync-daily.sh" >/dev/null 2>&1)
grep -q 'estructura vieja migrada' "$C7/.claude/sync.log" 2>/dev/null && ok "sync.log registra la migración" || bad "sync.log sin migración"
[ -f "$C7/doclens/doclens.py" ] && [ -f "$C7/doclens/.venv/MARK" ] && [ ! -e "$C7/scripts/doclens.py" ] && ok "auto-sync dejó doclens/ + venv movido" || bad "auto-sync no migró"
DOC=$(bash "$AI_TOOLING/scripts/harness-doctor.sh" "$C7" 2>&1)
case "$DOC" in *"WARN: doclens"*) bad "doctor aún avisa: $(grep 'doclens' <<< "$DOC" | head -1)" ;; *) ok "doctor sin aviso de doclens" ;; esac

echo "══ D13: motor real ══"
if [ -x "$AI_TOOLING/doclens/.venv/bin/python" ] && "$AI_TOOLING/doclens/.venv/bin/python" -c 'import fitz' 2>/dev/null; then
  "$AI_TOOLING/doclens/.venv/bin/python" -c "import fitz,sys; d=fitz.open(); d.new_page().insert_text((72,72),'hola'); d.save(sys.argv[1])" "$T/real.pdf"
  "$AI_TOOLING/doclens/.venv/bin/python" "$AI_TOOLING/doclens/doclens.py" "$T/real.pdf" >/dev/null 2>&1
  grep -qx '\*' "$T/real.pdf.inspect/.gitignore" 2>/dev/null && ok "el sidecar .inspect/ se ignora solo" || bad "sidecar sin .gitignore"
  mkdir -p "$T/ro"; cp "$T/real.pdf" "$T/ro/r.pdf"; chmod 555 "$T/ro"
  OUT=$("$AI_TOOLING/doclens/.venv/bin/python" "$AI_TOOLING/doclens/doclens.py" "$T/ro/r.pdf" 2>/dev/null); chmod 755 "$T/ro"
  D="${OUT#*INSPECT_DIR=}"
  [ -f "$D/index.md" ] && case "$D" in "$T/ro/"*) false ;; *) true ;; esac \
    && ok "carpeta de solo lectura → sidecar en un temporal" || bad "solo lectura: [$OUT]"
else
  echo "  - omitido: sin doclens/.venv real (corre bash doclens/sync.sh)"
fi
if command -v ruff >/dev/null 2>&1; then
  ruff check -q --no-cache "$AI_TOOLING/doclens/doclens.py" >/dev/null 2>&1 && ok "doclens.py limpio en ruff (no exporta avisos a consumers)" || bad "doclens.py con hallazgos de ruff"
else
  echo "  - omitido: ruff no instalado"
fi

# ── Segunda validación (revisión adversarial) ─────────────────────────────────
echo "══ D14: doclens/ como symlink — nunca se escribe ni borra a través de él ══"
C8=$(consumer); mkdir -p "$T/shared/doclens"; echo dato > "$T/shared/doclens/data.csv"; ln -s "$T/shared/doclens" "$C8/doclens"
OUT=$(bash "$SYNC" "$C8" 2>&1); RC=$?
eq "symlink a otra carpeta → exit 3" "$RC" 3
[ "$(cat "$T/shared/doclens/data.csv" 2>/dev/null)" = dato ] && [ ! -f "$T/shared/doclens/doclens.py" ] && ok "carpeta ajena intacta" || bad "escribió/borró a través del symlink"
C9=$(consumer); ln -s "$TB/doclens" "$C9/doclens"; echo nota > "$TB/doclens/NOTES-local.txt"
eq "symlink al propio toolbox → --check toolbox" "$(bash "$SYNC" "$C9" --check)" "toolbox: solo venv"
bash "$SYNC" "$C9" >/dev/null 2>&1
[ -f "$TB/doclens/NOTES-local.txt" ] && ok "no borra archivos del toolbox vía symlink" || bad "borró NOTES-local.txt del toolbox"
rm -f "$TB/doclens/NOTES-local.txt"

echo "══ D15: monorepo — el destino explícito se respeta (no sube a la raíz git) ══"
MONO=$(consumer); mkdir -p "$MONO/app"; stubvenv "$MONO/app/doclens/.venv"
bash "$SYNC" "$MONO/app" >/dev/null 2>&1
[ -f "$MONO/app/doclens/doclens.py" ] && [ ! -e "$MONO/doclens" ] && ok "doclens/ en el proyecto (mono/app), no en la raíz git" || bad "monorepo: $(ls -d "$MONO"/doclens "$MONO"/app/doclens 2>&1 | tr '\n' ' ')"
: > "$MONO/app/x.pdf"
R=$(dg "$MONO/app" "$MONO/app/x.pdf"); case "$R" in "2|"*pre-procesado*) ok "doc-guard lo encuentra (misma raíz que el sync)" ;; *) bad "doc-guard en monorepo: ${R%%$'\n'*}" ;; esac

echo "══ D16: DOCLENS_PYTHON = intérprete ya listo (mismo significado en sync y doc-guard) ══"
C10=$(consumer); bash "$SYNC" "$C10" --check >/dev/null
OUT=$(env DOCLENS_PYTHON="$STUB" bash "$SYNC" "$C10" 2>&1); RC=$?
eq "sync con DOCLENS_PYTHON sano → exit 0" "$RC" 0
has "lo usa en vez de crear venv" "$OUT" "usa DOCLENS_PYTHON"
[ ! -e "$C10/doclens/.venv" ] && ok "no crea doclens/.venv" || bad "creó venv con DOCLENS_PYTHON"
mkdir -p "$T/badpy/bin"; printf '#!/bin/sh\necho "ModuleNotFoundError: No module named fitz" >&2; exit 1\n' > "$T/badpy/bin/python"; chmod +x "$T/badpy/bin/python"
OUT=$(env DOCLENS_PYTHON="$T/badpy/bin/python" bash "$SYNC" "$C10" 2>&1); RC=$?
eq "DOCLENS_PYTHON sin dependencias → exit 1" "$RC" 1
has "explica qué falta" "$OUT" "no tiene las dependencias"
stubvenv "$C10/doclens/.venv"; : > "$T/d16.pdf"
R=$(dg "$C10" "$T/d16.pdf" DOCLENS_PYTHON="$T/badpy/bin/python"); case "$R" in "2|"*"DOCLENS_PYTHON="*) ok "doc-guard: mensaje nombra DOCLENS_PYTHON (no 'reinstala el venv')" ;; *) bad "doc-guard DOCLENS_PYTHON roto: ${R%%$'\n'*}" ;; esac

echo "══ D17: ruta con [corchetes] — el lock y .venv de otro proceso intactos ══"
CB="$T/proj [v2]"; mkdir -p "$CB" && (cd "$CB" && g init -q && touch R && g add -A && g commit -qm i) >/dev/null
stubvenv "$CB/doclens/.venv"; mkdir -p "$CB/doclens/.venv/emptydir" "$CB/doclens/.venv.lock"; echo $$ > "$CB/doclens/.venv.lock/pid"
bash "$SYNC" "$CB" >/dev/null 2>&1
[ -d "$CB/doclens/.venv.lock" ] && [ -d "$CB/doclens/.venv/emptydir" ] && ok "lock y .venv intactos con '[' en la ruta" || bad "borró lock o .venv por glob"

echo "══ D18: el espejo es lo COMMITEADO del toolbox, no su working tree ══"
echo nota > "$TB/doclens/zz-notes.md"; (cd "$TB" && g add -A && g commit -qm zz) >/dev/null
rm "$TB/doclens/zz-notes.md"; echo "edición sin commit" >> "$TB/doclens/README.md"; echo nuevo > "$TB/doclens/untracked.txt"
C11=$(consumer); stubvenv "$C11/doclens/.venv"
bash "$SYNC" "$C11" >/dev/null 2>&1; RC=$?
eq "último archivo borrado del working tree → exit 0 (no muere por pipefail)" "$RC" 0
[ -f "$C11/doclens/zz-notes.md" ] && ok "archivo commiteado llega aunque falte en el working tree" || bad "zz-notes.md no llegó"
! grep -q "edición sin commit" "$C11/doclens/README.md" && ok "edición sin commitear NO se propaga" || bad "propagó edición sin commit"
[ ! -e "$C11/doclens/untracked.txt" ] && ok "archivo sin versionar NO se propaga" || bad "propagó untracked"
(cd "$TB" && g checkout -q -- doclens && rm -f doclens/untracked.txt)
[ -x "$C11/doclens/sync.sh" ] && ok "sync.sh conserva el bit de ejecución" || echo "  - (sync.sh no es ejecutable en el toolbox: sin verificar)"

echo "══ D19: lock de un proceso muerto → se libera de inmediato ══"
C12=$(consumer); cp -R "$TB/doclens" "$C12/doclens"; rm -rf "$C12/doclens/.venv"
sh -c 'exit 0' & DEAD=$!; wait "$DEAD"
mkdir "$C12/doclens/.venv.lock"; echo "$DEAD" > "$C12/doclens/.venv.lock/pid"
env PATH="$UVPATH" bash "$C12/doclens/sync.sh" "$C12" --venv >/dev/null 2>&1; RC=$?
eq "PID muerto → toma el lock y crea el venv" "$RC" 0
rm -rf "$C12/doclens/.venv"; mkdir "$C12/doclens/.venv.lock"; echo "$DEAD" > "$C12/doclens/.venv.lock/pid"; : > "$T/d19.pdf"
R=$(dg "$C12" "$T/d19.pdf"); case "$R" in "2|"*pre-procesado*) ok "doc-guard no espera 10 min por un lock huérfano" ;; *) bad "lock huérfano en doc-guard: ${R%%$'\n'*}" ;; esac

echo "══ D20: doc-guard nunca alcanza el timeout del hook (fail-open) ══"
C13=$(consumer); mkdir -p "$C13/doclens/.venv/bin"; : > "$C13/doclens/doclens.py"
cat > "$C13/doclens/.venv/bin/python" <<EOF
#!/bin/sh
[ "\$1" = -c ] && exit 0
echo x >> "$T/slow.calls"; sleep 4
d="\$2.inspect"; mkdir -p "\$d"; : > "\$d/index.md"; echo "INSPECT_DIR=\$d"
EOF
chmod +x "$C13/doclens/.venv/bin/python"; : > "$T/grande.pdf"; : > "$T/slow.calls"
S0=$SECONDS; R=$(dg "$C13" "$T/grande.pdf" DOCLENS_HOOK_WAIT=1); EL=$((SECONDS - S0))
case "$R" in "2|"*"reintenta el Read"*) ok "documento lento → bloquea con 'reintenta' (exit 2)" ;; *) bad "lento: ${R%%$'\n'*}" ;; esac
[ "$EL" -le 3 ] && ok "respeta el plazo (${EL}s, no espera al proceso)" || bad "esperó ${EL}s"
R=$(dg "$C13" "$T/grande.pdf" DOCLENS_HOOK_WAIT=1)
eq "2º Read mientras procesa → no lanza otro proceso" "$(wc -l < "$T/slow.calls" | tr -d ' ')" 1
sleep 4
R=$(dg "$C13" "$T/grande.pdf"); case "$R" in "2|"*pre-procesado*) ok "al terminar en segundo plano → digest listo" ;; *) bad "después: ${R%%$'\n'*}" ;; esac

echo "══ D21: falla del ARCHIVO (no del venv) → mensaje correcto, sin reinstalar ══"
C14=$(consumer); mkdir -p "$C14/doclens/.venv/bin"; : > "$C14/doclens/doclens.py"; cp "$SYNC" "$C14/doclens/sync.sh"
printf '#!/bin/sh\n[ "$1" = -c ] && exit 0\necho "RuntimeError: cannot open broken document" >&2; exit 1\n' > "$C14/doclens/.venv/bin/python"
chmod +x "$C14/doclens/.venv/bin/python"; : > "$T/danado.pdf"; : > "$UVLOG"
R=$(dg "$C14" "$T/danado.pdf"); case "$R" in "2|"*"no pudo procesar este archivo"*"cannot open broken"*) ok "dice que el archivo falló (con el error de doclens)" ;; *) bad "archivo dañado: ${R%%$'\n'*}" ;; esac
[ ! -s "$UVLOG" ] && ok "no intenta reinstalar el venv" || bad "relanzó la creación del venv"

echo "══ D22: sin jq, PDF dentro de *.inspect/, toolbox sin git ══"
mkdir -p "$T/nojq"; for b in grep cat; do ln -sf "$(command -v $b)" "$T/nojq/$b"; done
R=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/x/a.pdf"}}' | PATH="$T/nojq" "$BASH" "$H/doc-guard.sh" 2>&1 >/dev/null; echo "|$?")
case "$R" in *"falta jq"*"|2") ok "sin jq: el PDF se bloquea igual" ;; *) bad "sin jq: [$R]" ;; esac
R=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/x/a.md"}}' | PATH="$T/nojq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq: lo que no es PDF pasa" "$R" 0
mkdir -p "$T/rep.inspect/raw"; : > "$T/rep.inspect/raw/x.pdf"
R=$(dg "$C1" "$T/rep.inspect/raw/x.pdf"); case "$R" in "2|"*) ok "PDF real dentro de *.inspect/ → se intercepta" ;; *) bad "PDF en .inspect pasó: ${R%%|*}" ;; esac
eq "md dentro de *.inspect/ → libre" "$(dg "$C1" "$T/rep.inspect/index.md" | cut -d'|' -f1)" 0
mkdir -p "$T/tbfs"; (cd "$TB" && git archive HEAD doclens) | tar -xf - -C "$T/tbfs"
: > "$T/tbfs/doclens/.DS_Store"; : > "$T/tbfs/doclens/doclens.py.orig"; mkdir -p "$T/tbfs/doclens/.ruff_cache"; : > "$T/tbfs/doclens/.ruff_cache/x"
C15=$(consumer); stubvenv "$C15/doclens/.venv"; bash "$T/tbfs/doclens/sync.sh" "$C15" >/dev/null 2>&1
[ -f "$C15/doclens/doclens.py" ] && [ ! -e "$C15/doclens/.DS_Store" ] && [ ! -e "$C15/doclens/doclens.py.orig" ] && [ ! -e "$C15/doclens/.ruff_cache" ] \
  && ok "toolbox sin git: copia sin basura (.DS_Store, *.orig, caches)" || bad "copió basura: $(cd "$C15/doclens" && ls -a | tr '\n' ' ')"

echo ""
echo "══ doclens: $PASS ok, $FAIL fallos ══"
rm -rf "$T"
[ "$FAIL" -eq 0 ]
