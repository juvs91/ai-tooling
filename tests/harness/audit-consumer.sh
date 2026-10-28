#!/usr/bin/env bash
# tests/harness/audit-consumer.sh — auditoría de un consumer del harness (ADR-0072).
# Complementa run-tests.sh (que corre contra fixtures): esto audita un repo real.
#
# Uso: bash tests/harness/audit-consumer.sh [ruta-consumer]
#      (default: /Volumes/case-sensitive/school-system)
#
# Read-only contra el consumer: solo escribe en fixtures temporales (/tmp).
set -uo pipefail

TOOLBOX="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONSUMER="${1:-/Volumes/case-sensitive/school-system}"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1"; }

[ -d "$CONSUMER" ] || { echo "consumer no existe: $CONSUMER"; exit 1; }
echo "Auditoría harness — consumer: $CONSUMER"
echo "                     toolbox:  $TOOLBOX"
echo ""

echo "══ A1: suite de regresión del toolbox (fixtures) ══"
OUT=$(bash "$TOOLBOX/tests/harness/run-tests.sh" 2>&1)
grep -qE "[0-9]+ ok, 0 fallos" <<< "$OUT" && ok "suite $(grep -oE '[0-9]+ ok, 0 fallos' <<< "$OUT")" || { bad "suite falló"; echo "$OUT" | tail -8; }

echo "══ A2: idempotencia — doble --apply en fixture ══"
FIX=$(mktemp -d)/consumer; mkdir -p "$FIX/.claude/hooks"
echo "{\"local_path\": \"$TOOLBOX\", \"sync_skills\": false, \"sync_doclens\": false}" > "$FIX/.ai-tooling"
bash "$TOOLBOX/scripts/install-hooks.sh" "$FIX" --apply >/dev/null 2>&1
SNAP1=$(find "$FIX" -type f ! -path "*/.harness-backup-*" | sort | xargs shasum -a 256 2>/dev/null | shasum -a 256)
OUT2=$(bash "$TOOLBOX/scripts/install-hooks.sh" "$FIX" --apply 2>&1)
SNAP2=$(find "$FIX" -type f ! -path "*/.harness-backup-*" | sort | xargs shasum -a 256 2>/dev/null | shasum -a 256)
[ "$SNAP1" = "$SNAP2" ] && ok "segundo apply: estado idéntico" || bad "segundo apply mutó estado"
grep -q "ACTUALIZAR\|INSTALAR" <<< "$OUT2" && bad "segundo apply aún copia cosas" || ok "segundo apply: todo al día"
BK_COUNT=$(ls -d "$FIX/.claude/".harness-backup-*/ 2>/dev/null | wc -l | tr -d ' ')
bash "$TOOLBOX/scripts/install-hooks.sh" "$FIX" --apply >/dev/null 2>&1
BK_COUNT2=$(ls -d "$FIX/.claude/".harness-backup-*/ 2>/dev/null | wc -l | tr -d ' ')
[ "$BK_COUNT" = "$BK_COUNT2" ] && ok "corrida al día no genera backups nuevos" || bad "backups: $BK_COUNT -> $BK_COUNT2"

echo "══ A3: settings.local.json generado es JSON válido y mergeable ══"
python3 -c "
import json; s = json.load(open('$FIX/.claude/settings.local.json'))
assert 'hooks' in s, 'estructura rota'
cmds = [h['command'] for g in s['hooks'].values() for e in g for h in e['hooks']]
assert all('CLAUDE_PROJECT_DIR' in c for c in cmds), 'rutas no robustas'
print(f'  estructura OK: {sum(len(v) for v in s[\"hooks\"].values())} grupos')" \
  && ok "settings.local.json válido" || bad "settings.local.json inválido"

echo "══ A4: backup preserva el contenido local pisado (--force) ══"
echo "# custom" >> "$FIX/.claude/hooks/block-dangerous.sh"
bash "$TOOLBOX/scripts/install-hooks.sh" "$FIX" --apply --force >/dev/null 2>&1
BK=$(ls -dt "$FIX/.claude/".harness-backup-*/ | head -1)
grep -q "# custom" "$BK/block-dangerous.sh.bak" 2>/dev/null && ok "backup preserva la versión local" || bad "backup sin contenido local"
rm -rf "$(dirname "$FIX")"

echo "══ A5: sintaxis y JSON de lo instalado en el consumer ══"
for f in "$CONSUMER/.claude/hooks/auto-sync-daily.sh" "$CONSUMER/scripts/harness-doctor.sh" "$CONSUMER/scripts/sync-doclens.sh"; do
  [ -f "$f" ] && { bash -n "$f" 2>/dev/null && ok "bash -n: $(basename $f)" || bad "sintaxis: $f"; } || ok "ausente (no aplica): $(basename $f)"
done
for f in "$CONSUMER/.claude/settings.json" "$CONSUMER/.claude/settings.local.json" "$CONSUMER/.ai-tooling"; do
  [ -f "$f" ] || { ok "ausente (no aplica): $(basename $f)"; continue; }
  python3 -c "import json; json.load(open('$f'))" 2>/dev/null && ok "JSON válido: $(basename $f)" || bad "JSON inválido: $f"
done

echo "══ A6: consistencia doctor vs install-hooks (mismo universo drift) ══"
if [ -f "$CONSUMER/scripts/harness-doctor.sh" ]; then
  DRIFT=$(bash "$CONSUMER/scripts/harness-doctor.sh" "$CONSUMER" 2>/dev/null | grep "drift informativo" | sed 's/.*hooks: //' | tr ' ' '\n' | sort)
  SALTAR=$(bash "$TOOLBOX/scripts/install-hooks.sh" "$CONSUMER" 2>/dev/null | grep "SALTAR     .claude/hooks/" | grep -oE '[a-z0-9-]+\.sh' | sort -u)
  [ "$DRIFT" = "$SALTAR" ] && ok "drift doctor == SALTAR install-hooks ($(echo $DRIFT | wc -w | tr -d ' ') hooks)" || { bad "inconsistencia"; echo "doctor: $DRIFT"; echo "install: $SALTAR"; }
else
  ok "doctor no instalado en consumer (skip)"
fi

echo "══ A7: harness completo en consumer — doble corrida, sin side effects ══"
if [ -f "$CONSUMER/.claude/hooks/auto-sync-daily.sh" ] && git -C "$CONSUMER" rev-parse >/dev/null 2>&1; then
  GIT_BEFORE=$(git -C "$CONSUMER" status --porcelain | shasum -a 256)
  echo "{\"cwd\":\"$CONSUMER\"}" | bash "$CONSUMER/.claude/hooks/auto-sync-daily.sh" >/dev/null 2>&1; E1=$?
  echo "{\"cwd\":\"$CONSUMER\"}" | bash "$CONSUMER/.claude/hooks/auto-sync-daily.sh" >/dev/null 2>&1; E2=$?
  GIT_AFTER=$(git -C "$CONSUMER" status --porcelain | shasum -a 256)
  [ "$E1" = "0" ] && [ "$E2" = "0" ] && ok "doble corrida exit 0" || bad "exit codes: $E1/$E2"
  [ "$GIT_BEFORE" = "$GIT_AFTER" ] && ok "working tree intacto tras 2 corridas" || bad "el harness tocó el working tree"
else
  ok "consumer sin auto-sync-daily o sin git (skip)"
fi

echo ""
echo "══ TOTAL: $PASS ok, $FAIL fallos ══"
[ "$FAIL" -eq 0 ]
