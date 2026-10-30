#!/usr/bin/env bash
# tests/harness/bypass-gate-tests.sh — protect-skill-gate-bypass.sh (ADR-0074).
# Los nombres protegidos se arman en runtime: escritos literales junto a un `>`
# este mismo hook bloquearía los comandos con los que se corre la suite.
set -uo pipefail

AI_TOOLING="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$AI_TOOLING/.claude/hooks/protect-skill-gate-bypass.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1"; }

G=".claude/hooks/skill-load""-gate.sh"   # gate
M=".claude/no-skill""-gate"              # marker
T=$(mktemp -d)

run_bash() { jq -n --arg c "$1" --arg d "$T" '{tool_name:"Bash",tool_input:{command:$c},cwd:$d}' | bash "$HOOK" >/dev/null 2>&1; echo $?; }
allow() { local rc; rc=$(run_bash "$1"); [ "$rc" = 0 ] && ok "permitido: $1" || bad "debió permitirse (rc=$rc): $1"; }
deny()  { local rc; rc=$(run_bash "$1"); [ "$rc" = 2 ] && ok "bloqueado: $1" || bad "debió bloquearse (rc=$rc): $1"; }

echo "══ B1: lecturas y escrituras a OTRO destino → permitidas ══"
allow "cat $G 2>/dev/null"
allow "ls $G 2>&1"
allow "sed -n 1p $G > /tmp/x"
allow "cp $G /tmp/x"
allow "grep x $G | tee /tmp/y"
allow "git log -- $G"
allow "git diff -- $G"
allow "git add $G"
allow "diff $G /tmp/x >/dev/null"
allow "grep -ln foo $G"
allow "docker run --rm img cat $G"
allow "git format-patch -1 -- $G"
allow "grep skill-load""-gate scripts/install-hooks.sh"
allow "echo hola"

echo "══ B2: redirecciones hacia el archivo protegido → bloqueadas ══"
deny "echo x > $M"
deny "echo x >> $G"
deny "echo x >| $M"
deny "echo x >& $M"
deny "echo x &> $M"
deny "ls 2> $M"
deny "exec 3> $M"
deny "exec 3<> $M"
deny "cat > $M <<'EOF'
x
EOF"
deny "echo x > \"$M\""
deny "echo x > \"/tmp/q b/../$G\""
deny "bash -c 'echo > $M'"

echo "══ B3: comandos de escritura sobre el archivo → bloqueados ══"
deny "tee -a $G < /dev/null"
deny "cp /tmp/x $G"
deny "cp /tmp/x/skill-load""-gate.sh .claude/hooks/"
mkdir -p "$T/.claude/hooks"
deny "cp /tmp/x/skill-load""-gate.sh .claude/hooks"
deny "mv $G /tmp/"
deny "mv /tmp/x $G"
deny "rm $G"
deny "unlink $G"
deny "truncate -s0 $G"
deny "chmod -x $G"
deny "touch $M"
deny "ln -sf /tmp/x $M"
deny "install /dev/null $M"
deny "dd if=/dev/null of=$M"
deny "sed -i s/a/b/ $G"
deny "sed -Ei s/a/b/ $G"
deny "perl -pi -e s/a/b/ $G"
deny "python3 -c \"open('$M','w')\""
deny "find .claude -name no-skill""-gate -delete"
deny "git checkout HEAD~1 -- $G"

echo "══ B4: evasiones conocidas → bloqueadas ══"
deny "echo $M | xargs touch"
deny "F=$M; touch \$F"
deny "F=$M; echo > \$F"
deny "echo hi
touch $M"
deny "x=\$(touch $M)"
deny "true >/tmp/x;touch $M"
deny "cat a 2>/dev/null&&rm $G"
deny "echo hi>/tmp/q|tee $M"
deny "if true; then touch $M; fi"
deny "{ touch $M; }"
deny "nohup touch $M"
deny "\\rm $G"
deny "/usr/bin/touch $M"

echo "══ B5: Write/Edit sin cambios ══"
for tool in Write Edit; do
  rc=$(jq -n --arg t "$tool" --arg f "$AI_TOOLING/$G" '{tool_name:$t,tool_input:{file_path:$f}}' | bash "$HOOK" >/dev/null 2>&1; echo $?)
  [ "$rc" = 2 ] && ok "$tool sobre el gate → 2" || bad "$tool sobre el gate (rc=$rc)"
done

echo ""
echo "══ Bypass gate: $PASS ok, $FAIL fallos ══"
rm -rf "$T"
[ "$FAIL" -eq 0 ]
