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

echo ""
echo "══ Resultado: $PASS ok, $FAIL fallos ══"
rm -rf "$(dirname "$FIX")"

echo ""
bash "$AI_TOOLING/tests/harness/worktree-tests.sh"; WT_RC=$?

[ "$FAIL" -eq 0 ] && [ "$WT_RC" -eq 0 ]
