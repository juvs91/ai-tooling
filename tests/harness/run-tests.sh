#!/usr/bin/env bash
# tests/harness/run-tests.sh — regresiones del harness (ADR-0072 M4).
# Usa el ai-tooling real como toolbox y un consumer fixture en /tmp.
# Uso: bash tests/harness/run-tests.sh
set -uo pipefail

AI_TOOLING="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIX=$(mktemp -d)/consumer
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1"; }
assert_file_absent()  { [ ! -e "$1" ] && ok "$2" || bad "$2 (existe: $1)"; }
assert_file_exists()  { [ -e "$1" ] && ok "$2" || bad "$2 (no existe: $1)"; }
assert_contains()     { grep -q "$2" "$1" 2>/dev/null && ok "$3" || bad "$3 ('$2' no está en $1)"; }
assert_not_contains() { grep -q "$2" "$1" 2>/dev/null && bad "$3 ('$2' no debería estar en $1)" || ok "$3"; }

new_fixture() {
  rm -rf "$FIX"; mkdir -p "$FIX/.claude/hooks" "$FIX/scripts"
  cat > "$FIX/.ai-tooling" <<EOF
{"local_path": "$AI_TOOLING", "sync_skills": false, "sync_doclens": false, "sync_hooks": true}
EOF
}

echo "══ T1: install-hooks default es dry-run (0 escrituras) ══"
new_fixture
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" >/dev/null 2>&1
assert_file_absent "$FIX/.claude/hooks/block-dangerous.sh" "dry-run no copia hooks"
assert_file_absent "$FIX/.claude/settings.local.json" "dry-run no crea settings.local.json"
assert_file_absent "$FIX/.claude/.harness-manifest.json" "dry-run no crea manifest"

echo "══ T2: --apply instala hooks + settings + manifest ══"
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply >/dev/null 2>&1
assert_file_exists "$FIX/.claude/hooks/block-dangerous.sh" "apply copia hooks"
assert_file_exists "$FIX/.claude/settings.local.json" "apply crea settings.local.json"
assert_file_exists "$FIX/.claude/.harness-manifest.json" "apply crea manifest"
assert_contains "$FIX/.claude/.harness-manifest.json" '"origin": "harness"' "manifest marca origin harness"

echo "══ T3: modificación local NO se pisa en re-apply ══"
echo "# custom local" >> "$FIX/.claude/hooks/block-dangerous.sh"
OUT=$(bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply 2>&1)
grep -q "modificado localmente" <<< "$OUT" && ok "reporta modificado localmente" || bad "no reportó modificación local"
assert_contains "$FIX/.claude/hooks/block-dangerous.sh" "# custom local" "la modificación local sobrevive"
# con --force sí sobrescribe
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply --force >/dev/null 2>&1
assert_not_contains "$FIX/.claude/hooks/block-dangerous.sh" "# custom local" "--force sí sobrescribe"
ls -d "$FIX/.claude/".harness-backup-*/ >/dev/null 2>&1 && ok "backup dir existe" || bad "no se generó backup"
grep -q "# custom local" "$FIX/.claude/".harness-backup-*/block-dangerous.sh.bak 2>/dev/null && ok "backup preserva la versión local" || bad "backup sin contenido local"

echo "══ T4: dedupe — hook en settings.json no se duplica en settings.local.json ══"
new_fixture
cat > "$FIX/.claude/settings.json" <<'EOF'
{"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "bash .claude/hooks/block-dangerous.sh", "timeout": 5}]}]}}
EOF
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply >/dev/null 2>&1
assert_not_contains "$FIX/.claude/settings.local.json" "block-dangerous.sh" "block-dangerous no duplicado en settings.local.json"

echo "══ T5: regresión jq — sync_hooks:false respeta el opt-out ══"
new_fixture
python3 - <<EOF
import json
m = json.load(open("$FIX/.ai-tooling")); m["sync_hooks"] = False
json.dump(m, open("$FIX/.ai-tooling", "w"))
EOF
echo "{\"cwd\":\"$FIX\"}" | CLAUDE_PROJECT_DIR="$FIX" bash "$AI_TOOLING/.claude/hooks/auto-sync-daily.sh" >/dev/null 2>&1
assert_file_absent "$FIX/.claude/settings.local.json" "sync_hooks:false → install-hooks NO corre"
grep -q "doctor:" "$FIX/.claude/sync.log" 2>/dev/null && ok "doctor corre y loguea aunque hooks esté off" || bad "doctor no logueó"

echo "══ T6: doctor — librería sourceada no es huérfana; substring no es referencia ══"
new_fixture
cat > "$FIX/.claude/hooks/a-hook.sh" <<'EOF'
#!/bin/bash
LIB="$CWD/.claude/hooks/a-lib.sh"
source "$LIB"
EOF
echo "#!/bin/bash" > "$FIX/.claude/hooks/a-lib.sh"
echo "#!/bin/bash" > "$FIX/.claude/hooks/quality-gate.sh"
echo "# ts-quality-gate.sh menciona su propio nombre" > "$FIX/.claude/hooks/ts-quality-gate.sh"
OUT=$(bash "$AI_TOOLING/scripts/harness-doctor.sh" "$FIX" 2>&1)
grep -q "a-lib.sh" <<< "$(grep huérfanos <<< "$OUT")" && bad "a-lib.sh marcada huérfana (es librería)" || ok "a-lib.sh (librería) no es huérfana"
grep -q "quality-gate.sh" <<< "$(grep huérfanos <<< "$OUT")" && ok "quality-gate.sh sí es huérfana (ts-quality-gate no la referencia)" || bad "quality-gate.sh falsamente excluida por substring"

echo "══ T7: backup solo de untracked — git ya cubre lo trackeado ══"
new_fixture
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply >/dev/null 2>&1
(
  cd "$FIX"
  git init -q
  git add .claude/hooks scripts
  git -c user.name=t -c user.email=t@t commit -qm init
)
echo "# custom local" >> "$FIX/.claude/hooks/block-dangerous.sh"
# Forzar que settings.local.json CAMBIE en esta corrida (si no cambia, no se
# respalda — ese es el comportamiento correcto desde el fix de idempotencia)
python3 - <<EOF
import json
p = "$FIX/.claude/settings.local.json"
s = json.load(open(p)); s["hooks"] = {}
json.dump(s, open(p, "w"))
EOF
bash "$AI_TOOLING/scripts/install-hooks.sh" "$FIX" --apply --force >/dev/null 2>&1
BK=$(ls -dt "$FIX/.claude/".harness-backup-*/ 2>/dev/null | head -1)
[ -n "$BK" ] || bad "no se generó backup dir"
if [ -n "$BK" ]; then
  [ -f "$BK/block-dangerous.sh.bak" ] && bad "hook trackeado respaldado (redundante con git)" || ok "hook trackeado NO se respalda (git lo cubre)"
  [ -f "$BK/settings.local.json.bak" ] && ok "settings.local.json (untracked, con cambios) sí se respalda" || bad "settings.local.json untracked sin respaldo"
fi

IH="$AI_TOOLING/scripts/install-hooks.sh"
cnt() { jq --arg p "$2" '[(.hooks // {})[][].hooks[].command | select(test($p))] | length' "$1"; }

echo "══ T8: merge — hooks propios del consumer sobreviven (ADR-0074) ══"
new_fixture
cat > "$FIX/.claude/settings.local.json" <<'EOF'
{"permissions":{"allow":["Bash(ls)"]},
 "hooks":{"PreToolUse":[
   {"matcher":"","hooks":[
     {"type":"command","command":"\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/block-dangerous.sh","timeout":5},
     {"type":"command","command":"bash .claude/hooks/my-hook.sh","timeout":5}]},
   {"matcher":"Edit|Write","hooks":[{"type":"command","command":"bash .claude/hooks/scope-gate.sh","timeout":5}]}]}}
EOF
bash "$IH" "$FIX" --apply >/dev/null 2>&1
bash "$IH" "$FIX" --apply >/dev/null 2>&1
S="$FIX/.claude/settings.local.json"
[ "$(cnt "$S" 'my-hook\.sh')" = 1 ] && ok "hook propio en grupo mixto se conserva (1 vez)" || bad "hook propio perdido o duplicado ($(cnt "$S" 'my-hook\.sh'))"
[ "$(cnt "$S" 'block-dangerous\.sh')" = 1 ] && ok "hook del harness registrado una sola vez" || bad "block-dangerous registrado $(cnt "$S" 'block-dangerous\.sh') veces"
[ "$(cnt "$S" 'scope-gate\.sh')" = 1 ] && ok "forma relativa del harness reemplazada (sin doble ejecución)" || bad "scope-gate registrado $(cnt "$S" 'scope-gate\.sh') veces"
[ "$(jq -r '.permissions.allow[0]' "$S")" = "Bash(ls)" ] && ok "resto de settings.local.json preservado" || bad "permissions perdidas"
[ "$(jq -r '.registered | length > 0' "$FIX/.claude/.harness-manifest.json")" = true ] && ok "manifest.registered persistido" || bad "manifest.registered ausente"

echo "══ T9: sin cambios → no reescribe ni crea backup ══"
B0=$(ls -d "$FIX"/.claude/.harness-backup-* 2>/dev/null | wc -l | tr -d ' ')
CK0=$(cksum < "$S"); REF="$(dirname "$FIX")/t9-ref"; sleep 1; : > "$REF"; sleep 1
OUT=$(bash "$IH" "$FIX" --apply 2>&1)
B1=$(ls -d "$FIX"/.claude/.harness-backup-* 2>/dev/null | wc -l | tr -d ' ')
# cksum no detecta una reescritura con el mismo contenido; -newer sí
[ "$CK0" = "$(cksum < "$S")" ] && [ -z "$(find "$S" -newer "$REF")" ] && ok "settings.local.json intacto (contenido y mtime)" || bad "settings.local.json reescrito sin cambios"
[ "$B0" = "$B1" ] && ok "sin backup nuevo" || bad "backup creado sin cambios ($B0 → $B1)"
grep -q "sin cambios" <<< "$OUT" && ok "reporta 'sin cambios'" || bad "no reporta 'sin cambios'"

echo "══ T10: settings.local.json inválido → aborta antes de copiar ══"
new_fixture
echo '{roto' > "$FIX/.claude/settings.local.json"
bash "$IH" "$FIX" --apply >/dev/null 2>&1; RC=$?
[ "$RC" != 0 ] && ok "exit ≠ 0" || bad "no abortó"
assert_file_absent "$FIX/.claude/hooks/block-dangerous.sh" "0 hooks copiados"

echo "══ T11: dry-run reporta preservados y no escribe ══"
new_fixture
echo '{"hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"bash mine.sh"}]}]}}' > "$FIX/.claude/settings.local.json"
CK0=$(cksum < "$FIX/.claude/settings.local.json")
OUT=$(bash "$IH" "$FIX" 2>&1)
grep -q "preservados (hooks propios del consumer): 1" <<< "$OUT" && ok "dry-run reporta 1 preservado" || bad "dry-run no reporta preservados"
[ "$CK0" = "$(cksum < "$FIX/.claude/settings.local.json")" ] && ok "dry-run no toca settings" || bad "dry-run escribió settings"
assert_file_absent "$FIX/.worktreeinclude" "dry-run no crea .worktreeinclude"

echo "══ T12: .worktreeinclude — crea, no pisa, respeta declinado ══"
new_fixture
bash "$IH" "$FIX" --apply >/dev/null 2>&1
assert_file_exists "$FIX/.worktreeinclude" "se crea si falta"
echo "propio" > "$FIX/.worktreeinclude"; bash "$IH" "$FIX" --apply >/dev/null 2>&1
grep -qx propio "$FIX/.worktreeinclude" && ok "uno propio no se toca" || bad "pisó el .worktreeinclude propio"
rm -f "$FIX/.worktreeinclude"; bash "$IH" "$FIX" --apply >/dev/null 2>&1
assert_file_absent "$FIX/.worktreeinclude" "borrado por el usuario no se recrea (declinado)"
bash "$IH" "$FIX" --apply --force >/dev/null 2>&1
assert_file_exists "$FIX/.worktreeinclude" "--force lo recrea"

echo "══ T13: /worktrees/ en .claude/.gitignore — limpio e idempotente ══"
new_fixture
printf 'sessions/' > "$FIX/.claude/.gitignore"   # sin salto de línea final
bash "$IH" "$FIX" --apply >/dev/null 2>&1; bash "$IH" "$FIX" --apply >/dev/null 2>&1
grep -qx 'sessions/' "$FIX/.claude/.gitignore" && ok "última línea previa intacta" || bad "se pegó a la línea anterior"
[ "$(grep -cx '/worktrees/' "$FIX/.claude/.gitignore")" = 1 ] && ok "/worktrees/ una sola vez" || bad "/worktrees/ duplicado o ausente"
sed -i.x '/^\/worktrees\/$/d' "$FIX/.claude/.gitignore"; bash "$IH" "$FIX" --apply >/dev/null 2>&1
! grep -qx '/worktrees/' "$FIX/.claude/.gitignore" && ok "quitado por el usuario no se re-agrega" || bad "re-agregó /worktrees/"

echo "══ T14: routing de skills — rutas existen, nombres únicos, tabla == name: ══"
# Las rutas viven copiadas a mano en 3 archivos: un borrado/movimiento de skill las
# rompe en silencio (pasó en b53b6ba con python-testing en workflow-coordinator.md).
SK="$AI_TOOLING/.agents/skills"; WC="$AI_TOOLING/.claude/commands/workflow-coordinator.md"
fm_name() { [ -f "$1" ] || return 0; awk 'NR==1&&!/^---/{exit} NR>1&&/^---/{exit} /^name:/{sub(/^name:[[:space:]]*/,""); gsub(/["'"'"']/,""); print; exit}' "$1"; }
MISS=""
for p in $(grep -ohE '\.agents/skills/[A-Za-z0-9_./-]+/SKILL\.md' "$WC" | sort -u); do
  [ -f "$AI_TOOLING/$p" ] || MISS="$MISS ${p#.agents/skills/}(wc)"
done
for p in $(grep -ohE '`[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+/SKILL\.md`' "$AI_TOOLING/AGENTS.md" "$SK/skills.md" | tr -d '`' | sed 's|^\.agents/skills/||' | sort -u); do
  [ -f "$SK/$p" ] || MISS="$MISS $p"
done
[ -z "$MISS" ] && ok "toda ruta de AGENTS.md, skills.md y workflow-coordinator.md existe" || bad "rutas rotas:$MISS"
DUP=$(find "$SK" -name SKILL.md | while read -r f; do fm_name "$f"; done | sort | uniq -d | tr '\n' ' ')
[ -z "$DUP" ] && ok "sin skills con name: duplicado" || bad "name: duplicado (OpenCode/Claude cargan uno al azar): $DUP"
MISM=$(grep -E '^\| .*\| `[^`]+` \| `\.agents/skills/' "$WC" | sed -E 's/.*\| `([^`]+)` \| `([^`]+)`.*/\1 \2/' |
  while read -r n p; do [ "$n" = "$(fm_name "$AI_TOOLING/$p")" ] || printf '%s ' "$n"; done)
[ -z "$MISM" ] && ok "tabla de workflow-coordinator.md == name: del skill" || bad "fila apunta a otro skill: $MISM"

echo ""
echo "══ Resultado: $PASS ok, $FAIL fallos ══"
rm -rf "$(dirname "$FIX")"

echo ""
bash "$AI_TOOLING/tests/harness/worktree-tests.sh"; WT_RC=$?
echo ""
bash "$AI_TOOLING/tests/harness/bypass-gate-tests.sh"; BG_RC=$?
echo ""
bash "$AI_TOOLING/tests/harness/githook-tests.sh"; GH_RC=$?
echo ""
bash "$AI_TOOLING/tests/harness/doclens-tests.sh"; DL_RC=$?

[ "$FAIL" -eq 0 ] && [ "$WT_RC" -eq 0 ] && [ "$BG_RC" -eq 0 ] && [ "$GH_RC" -eq 0 ] && [ "$DL_RC" -eq 0 ]
