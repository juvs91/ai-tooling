#!/usr/bin/env bash
# tests/harness/worktree-tests.sh — regresiones de worktrees y raíz de sesión (ADR-0073).
# Fixtures git reales en un tmp propio; usa los hooks/scripts del ai-tooling real.
# Uso: bash tests/harness/worktree-tests.sh   (también lo invoca run-tests.sh)
set -uo pipefail

AI_TOOLING="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
H="$AI_TOOLING/.claude/hooks"
T=$(mktemp -d); T=$(cd -P "$T" && pwd -P)
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1"; }
eq()  { [ "$2" = "$3" ] && ok "$1" || bad "$1 (got [$2] want [$3])"; }

unset GIT_DIR GIT_INDEX_FILE CLAUDE_PROJECT_DIR CLAUDE_CODE_SESSION_ID
g() { git -c user.name=t -c user.email=t@t -c commit.gpgsign=false -c protocol.file.allow=always "$@"; }
SID=test-sid-0073
# Armado en runtime: un literal así lo bloquearía protect-secrets al escribir este archivo
FAKE_SECRET="API""_KEY=$(printf 'x%.0s' $(seq 1 24))"

# ── Fixture: M (principal) + W (anidado) + S (hermano) + alias (symlink) + otro repo
mkdir -p "$T/M" && cd "$T/M" && g init -q && g symbolic-ref HEAD refs/heads/master   # init -b exige git ≥2.28
mkdir -p src apps/web .claude/hooks scripts tools docs/adr .agents/skills/x .agents/skills/workflow/workflow-coordinator
touch AGENTS.md .agents/skills/workflow/workflow-coordinator/SKILL.md   # skill-autoload exige ambos
cp "$H/task-scope-lib.sh" .claude/hooks/
cp "$AI_TOOLING/scripts/task-verify.sh" scripts/
cp "$AI_TOOLING/.agents/sync_skills.sh" .agents/
cp "$AI_TOOLING/tools/check_adr_gate.py" tools/
printf 'sessions/\n/worktrees/\n' > .claude/.gitignore
touch src/x.ts apps/web/a .agents/skills/x/SKILL.md docs/adr/ADR-0001-base.md
g add -A && g commit -qm init
g worktree add -q .claude/worktrees/w -b w
g worktree add -q "$T/S" -b s
ln -s "$T" "$T/alias"
mkdir -p "$T/other" && (cd "$T/other" && g init -q && touch f && g add f && g commit -qm i)
cd "$AI_TOOLING"
M="$T/M"; W="$M/.claude/worktrees/w"; S="$T/S"

echo "══ W1: lib — clasificación y raíz canónica ══"
(
  . "$H/task-scope-lib.sh"
  lw() { is_linked_worktree "$1" && echo yes || echo no; }
  eq "principal no es enlazado" "$(lw "$M")" no
  eq "worktree anidado es enlazado" "$(lw "$W")" yes
  eq "state_root desde subdir de W → M" "$(state_root "$W/src")" "$M"
  eq "state_root vía symlink en hermano → M" "$(state_root "$T/alias/S")" "$M"
  eq "map_to_main conserva subproyecto" "$(map_to_main "$W/apps/web")" "$M/apps/web"
  eq "project_root fuera de worktree = salida cruda" "$(CLAUDE_PROJECT_DIR="$T/alias/M" project_root /x)" "$T/alias/M"
  eq "project_root con CPD=worktree → principal" "$(CLAUDE_PROJECT_DIR="$W" project_root /x)" "$M"
  repo_of_file "$W/src/new/f.ts" /
  eq "repo_of_file en W → top W" "$RF_TOP" "$W"
  repo_of_file /nonexistent-0073/x /
  eq "repo_of_file fuera de repo → vacío" "$RF_TOP" ""
  exit $FAIL
) ; W1=$?
[ "$W1" -eq 0 ] || FAIL=$((FAIL+W1))

echo "══ W2: hooks de estado — todo cae en la raíz, sin .claude/ basura ══"
for hook in skill-autoload.sh plan-mode-gate.sh; do
  echo "{\"session_id\":\"$SID\",\"cwd\":\"$W/src\"}" | (cd "$W/src" && CLAUDE_PROJECT_DIR="$M" bash "$H/$hook" >/dev/null 2>&1)
done
echo "{\"session_id\":\"$SID\",\"cwd\":\"$M/src\",\"tool_input\":{\"file_path\":\"$M/.agents/skills/x/SKILL.md\"}}" \
  | (cd "$M/src" && CLAUDE_PROJECT_DIR="$M" bash "$H/track-skill-load.sh")
echo "{\"session_id\":\"$SID\",\"cwd\":\"$M/src\",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$M/src/x.ts\"}}" \
  | (cd "$M/src" && CLAUDE_PROJECT_DIR="$M" bash "$H/edit-drift-detector.sh" 2>/dev/null)
[ -f "$M/.claude/sessions/$SID-plan-gate" ] && ok "plan-gate marker en la raíz" || bad "plan-gate marker fuera de la raíz"
[ -f "$M/.claude/sessions/$SID" ] && ok "skill-autoload marker en la raíz" || bad "skill-autoload marker fuera de la raíz"
[ -f "$M/.claude/sessions/$SID-skill-loaded" ] && ok "skill-loaded en la raíz" || bad "skill-loaded fuera de la raíz"
[ -f "$M/.claude/drift-state/edit-count-$SID" ] && ok "contador de drift por sesión en la raíz" || bad "contador de drift fuera de la raíz"
[ ! -e "$M/src/.claude" ] && [ ! -e "$W/src/.claude" ] && ok "sin .claude/ basura en subdirectorios" || bad "se creó .claude/ en un subdirectorio"
eq "main-top registra el toplevel del hilo principal" "$(cat "$M/.claude/sessions/$SID-main-top" 2>/dev/null)" "$M"

echo "══ W3: skill-load-gate encuentra el marker de la raíz desde un subdir ══"
echo "{\"session_id\":\"$SID\",\"cwd\":\"$M/src\"}" | (cd "$M/src" && CLAUDE_PROJECT_DIR="$M" bash "$H/skill-load-gate.sh" >/dev/null 2>&1)
eq "skill-load-gate exit 0 tras cd" "$?" 0

echo "══ W4: track-skill-load — mismo .cwd no hace fork de git ══"
SHIM="$T/shim"; mkdir -p "$SHIM"; : > "$T/gitcalls"
printf '#!/bin/sh\necho x >> %s\nexec %s "$@"\n' "$T/gitcalls" "$(command -v git)" > "$SHIM/git"; chmod +x "$SHIM/git"
for i in 1 2; do
  echo "{\"session_id\":\"$SID\",\"cwd\":\"$M\",\"tool_input\":{}}" | (cd "$M" && PATH="$SHIM:$PATH" CLAUDE_PROJECT_DIR="$M" bash "$H/track-skill-load.sh")
done
: > "$T/gitcalls"
echo "{\"session_id\":\"$SID\",\"cwd\":\"$M\",\"tool_input\":{}}" | (cd "$M" && PATH="$SHIM:$PATH" CLAUDE_PROJECT_DIR="$M" bash "$H/track-skill-load.sh")
eq "0 invocaciones de git con cwd sin cambio" "$(wc -l < "$T/gitcalls" | tr -d ' ')" 0
echo "{\"session_id\":\"$SID\",\"cwd\":\"$W\",\"agent_id\":\"sub1\",\"tool_input\":{}}" | (cd "$W" && CLAUDE_PROJECT_DIR="$M" bash "$H/track-skill-load.sh")
eq "evento de subagente no toca main-top" "$(cat "$M/.claude/sessions/$SID-main-top")" "$M"

echo "══ W5: quality — clave compartida, worktrees independientes, tsc anclado ══"
mkdir -p "$M/node_modules/.bin" && echo '{}' > "$M/tsconfig.json"
TSC_OUT="$T/tsc.out"; TSC_RC="$T/tsc.rc"
printf '#!/bin/sh\ncat %s\nexit $(cat %s)\n' "$TSC_OUT" "$TSC_RC" > "$M/node_modules/.bin/tsc"; chmod +x "$M/node_modules/.bin/tsc"
qg() { echo "{\"cwd\":\"$1\",\"tool_input\":{\"file_path\":\"$2\"}}" | (cd "$1" && PATH=/usr/bin:/bin CLAUDE_PROJECT_DIR="$M" bash "$H/ts-quality-gate.sh" 2>/dev/null); }
qe() { echo "{\"cwd\":\"$1\",\"tool_input\":{\"file_path\":\"$2\"}}" | (cd "$1" && CLAUDE_PROJECT_DIR="$M" bash "$H/quality-enforce.sh" 2>/dev/null); echo $?; }
echo 'src/x.tsx(1,1): error TS1005: otro archivo' > "$TSC_OUT"; echo 2 > "$TSC_RC"
qg "$M/src" "$M/src/x.ts"
eq "x.ts no matchea errores de x.tsx" "$(qe "$M" "$M/src/x.ts")" 0
echo 'lib/src/x.ts(1,1): error TS2322: otro archivo con sufijo igual' > "$TSC_OUT"
qg "$M/src" "$M/src/x.ts"
eq "lib/src/x.ts no cuenta para src/x.ts (match anclado)" "$(qe "$M" "$M/src/x.ts")" 0
echo 'src/x.ts(1,1): error TS2322: bad' > "$TSC_OUT"
qg "$M/src" "$M/src/x.ts"
eq "error en x.ts (gate desde subdir) bloquea desde la raíz" "$(qe "$M" "$M/src/x.ts")" 2
eq "mismo src/x.ts en el worktree W no se bloquea" "$(qe "$W" "$W/src/x.ts")" 0
: > "$TSC_OUT"; echo 1 > "$TSC_RC"
qg "$M" "$M/src/x.ts"
eq "tsc roto sin diagnósticos no borra el estado" "$(qe "$M" "$M/src/x.ts")" 2
qg "$W" "$W/src/x.ts"   # W no tiene tsconfig ni node_modules propios → tope en su repo
eq "sin tsc en W el estado de M queda intacto" "$(qe "$M" "$M/src/x.ts")" 2
rm -rf "$M/.claude/quality-state"

echo "══ W6: scope-gate — worktrees, hermano, repo ajeno, symlink ══"
mkdir -p "$M/.claude/sessions"
sg() { echo "{\"session_id\":\"$SID\",\"cwd\":\"$1\",\"tool_input\":{\"file_path\":\"$2\"}}" | (cd "$1" && CLAUDE_PROJECT_DIR="${3:-$M}" bash "$H/scope-gate.sh" >/dev/null 2>&1); echo $?; }
echo '{"mode":"analysis","analysis_write_paths":["ai-notes/findings"]}' > "$M/.claude/sessions/$SID-task-scope.json"
eq "analysis: path permitido dentro de W → 0" "$(sg "$W" "$W/ai-notes/findings/a.md")" 0
eq "analysis: código dentro de W → 2" "$(sg "$W" "$W/src/x.ts")" 2
eq "analysis: repo ajeno fuera de jurisdicción → 0" "$(sg "$M" "$T/other/f")" 0
echo '{"mode":"validate"}' > "$M/.claude/sessions/$SID-task-scope.json"
eq "validate: hermano S ya no falla abierto → 2" "$(sg "$M" "$S/src/x.ts")" 2
eq "validate: ROOT vía symlink no falla abierto → 2" "$(sg "$T/alias/M" "$T/alias/M/src/x.ts" "$T/alias/M")" 2
eq "validate: editar el propio scope vía symlink → 0" "$(sg "$T/alias/M" "$T/alias/M/.claude/sessions/$SID-task-scope.json" "$T/alias/M")" 0
rm -f "$M/.claude/sessions/$SID-task-scope.json"

echo "══ W7: adr-gate y protect-secrets actúan sobre el repo del archivo ══"
ag() { echo "{\"cwd\":\"$M\",\"tool_input\":{\"file_path\":\"$1\"}}" | (cd "$M" && bash "$H/adr-gate.sh" >/dev/null 2>&1); echo $?; }
eq "skill en W sin ADR → 2" "$(ag "$W/.agents/skills/x/SKILL.md")" 2
eq "skill vía symlink (prefijo alterno) sin ADR → 2" "$(ag "$T/alias/M/.agents/skills/x/SKILL.md")" 2
printf '# ADR-0002\nToca .agents/skills/\n' > "$W/docs/adr/ADR-0002-x.md"
eq "skill en W con ADR untracked en W → 0" "$(ag "$W/.agents/skills/x/SKILL.md")" 0
(cd "$W" && g add docs/adr/ADR-0002-x.md) && echo "más" >> "$W/docs/adr/ADR-0002-x.md"
eq "ADR en estado AM sigue contando → 0" "$(ag "$W/.agents/skills/x/SKILL.md")" 0
(cd "$W" && g reset -q -- docs/adr/ADR-0002-x.md) && rm -f "$W/docs/adr/ADR-0002-x.md"
ps() { echo "{\"cwd\":\"$M\",\"tool_input\":{\"file_path\":\"$1\",\"content\":\"$FAKE_SECRET\"}}" | (cd "$M" && bash "$H/protect-secrets.sh" >/dev/null 2>&1); echo $?; }
eq "secreto en archivo trackeado de W anidado → 2" "$(ps "$W/src/conf.sh")" 2

echo "══ W8: nada que escriba archivos trackeados corre en un worktree ══"
(cd "$W" && bash .agents/sync_skills.sh >/dev/null 2>&1); eq "sync_skills en W → exit 0" "$?" 0
[ ! -e "$W/.agents/.last_sync" ] && ok "sync_skills no escribió en W" || bad "sync_skills escribió en W"
echo '{}' > "$W/.ai-tooling"
echo "{\"cwd\":\"$W\"}" | (cd "$W" && CLAUDE_PROJECT_DIR="$W" bash "$H/auto-sync-daily.sh" >/dev/null 2>&1)
[ ! -e "$W/.claude/sync.log" ] && ok "auto-sync-daily no corre en W" || bad "auto-sync-daily corrió en W"
rm -f "$W/.ai-tooling"

echo "══ W9: task-verify no borra el scope del padre desde un subagente ══"
printf '{"mode":"full","completion_checklist":["echo ok"]}\n' > "$M/.claude/sessions/$SID-task-scope.json"
echo "$M" > "$M/.claude/sessions/$SID-main-top"
(cd "$W" && CLAUDE_CODE_SESSION_ID="$SID" bash scripts/task-verify.sh >/dev/null 2>&1)
[ -f "$M/.claude/sessions/$SID-task-scope.json" ] && ok "desde W (subagente) el scope se conserva" || bad "desde W se borró el scope del padre"
(cd "$M/src" && CLAUDE_CODE_SESSION_ID="$SID" bash ../scripts/task-verify.sh >/dev/null 2>&1)
[ ! -f "$M/.claude/sessions/$SID-task-scope.json" ] && ok "desde el hilo principal sí se borra" || bad "el hilo principal no borró el scope"

echo "══ W10: block-dangerous con prefijo alterno (symlink) ══"
echo "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf $T/alias/M/.claude/worktrees/w\"}}" | (cd "$M" && bash "$H/block-dangerous.sh" >/dev/null 2>&1)
eq "rm -rf de un worktree vía symlink → 2" "$?" 2

echo "══ W11: worktree-isolation-gate → additionalContext sin permissionDecision ══"
OUT=$(echo '{"tool_name":"Workflow","tool_input":{"script":"await parallel([()=>agent(\"a\",{isolation:\"worktree\"}),()=>agent(\"b\")])"}}' | bash "$H/worktree-isolation-gate.sh")
[ "$(jq -r '.hookSpecificOutput.additionalContext | length > 0' <<< "$OUT")" = "true" ] && ok "emite additionalContext" || bad "no emite additionalContext"
[ "$(jq -r '.hookSpecificOutput | has("permissionDecision")' <<< "$OUT")" = "false" ] && ok "sin permissionDecision (no auto-aprueba)" || bad "emite permissionDecision"

echo "══ W12: install_hooks — commit-msg en el common dir, [skip-adr] del mensaje actual ══"
printf '#!/bin/sh\necho ajeno\n' > "$M/.git/hooks/pre-commit"; chmod +x "$M/.git/hooks/pre-commit"
(cd "$W" && bash "$AI_TOOLING/tools/install_hooks.sh" >/dev/null 2>&1 && bash "$AI_TOOLING/tools/install_hooks.sh" >/dev/null 2>&1)
[ -x "$M/.git/hooks/commit-msg" ] && ok "commit-msg instalado en el common dir desde W" || bad "commit-msg no instalado"
grep -q 'echo ajeno' "$M/.git/hooks/pre-commit" && ok "pre-commit ajeno sobrevive 2 corridas" || bad "pre-commit ajeno pisado"
cd "$W" && echo cambio >> .agents/skills/x/SKILL.md && g add .agents/skills/x/SKILL.md
g commit -qm "sin adr" >/dev/null 2>&1; eq "commit de skill sin ADR → bloqueado" "$?" 1
g commit -qm "trivial [skip-adr]" >/dev/null 2>&1; eq "[skip-adr] del mensaje ACTUAL → pasa" "$?" 0
cd "$AI_TOOLING"

echo "══ W13: doctor — worktree nuevo no es 'mergeado' ══"
echo '{}' > "$M/.ai-tooling"
OUT=$(bash "$AI_TOOLING/scripts/harness-doctor.sh" "$M" 2>&1)
grep -qE 'mergeada.*[[:space:]]s([[:space:]]|$)' <<< "$OUT" && bad "worktree S recién creado marcado como mergeado" || ok "worktree recién creado no es candidato"
(cd "$S" && echo z > z && g add z && g commit -qm own) && (cd "$M" && g merge -q --no-edit s)
OUT=$(bash "$AI_TOOLING/scripts/harness-doctor.sh" "$M" 2>&1)
grep -qE 'mergeada.*[[:space:]]s([[:space:]]|$)' <<< "$OUT" && ok "rama con commit propio ya mergeada sí es candidata" || bad "rama mergeada no detectada"
rm -f "$M/.ai-tooling"

echo "══ W14: doc-guard — venv desde la raíz del proyecto, tamaño portable ══"
# venv falso (gitignorado en la vida real → solo existe en M, nunca en W): simula doclens
mkdir -p "$M/scripts/.venv-doc-tools/bin"; touch "$M/scripts/doclens.py"
printf '#!/bin/sh\nd="$2.inspect"; mkdir -p "$d"; : > "$d/index.md"; : > "$d/downscaled.png"; echo "INSPECT_DIR=$d"\n' > "$M/scripts/.venv-doc-tools/bin/python"
chmod +x "$M/scripts/.venv-doc-tools/bin/python"
: > "$T/doc.pdf"; : > "$T/small.png"; dd if=/dev/zero of="$T/big.png" bs=1048576 count=9 2>/dev/null
dg() {  # dg <cwd> <file> → "rc|stderr"
  local e rc; e=$(echo "{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$2\"}}" | (cd "$1" && CLAUDE_PROJECT_DIR="$M" bash "$H/doc-guard.sh" 2>&1 >/dev/null)); rc=$?
  echo "$rc|$e"
}
R=$(dg "$M/src" "$T/doc.pdf"); case "$R" in "2|"*pre-procesado*) ok "PDF tras cd a subdir → pre-procesado (no fallback manual)" ;; *) bad "PDF desde subdir: ${R%%$'\n'*}" ;; esac
R=$(dg "$W" "$T/doc.pdf");     case "$R" in "2|"*pre-procesado*) ok "PDF desde worktree → usa el venv de la raíz" ;; *) bad "PDF desde worktree: ${R%%$'\n'*}" ;; esac
R=$(dg "$M" "$T/big.png");     case "$R" in "2|"*downscaled*) ok "imagen de 9 MB → downscaled (tamaño portable)" ;; *) bad "imagen grande no interceptada: ${R%%$'\n'*}" ;; esac
R=$(dg "$M" "$T/small.png");   eq "imagen chica → permitida" "${R%%|*}" 0
R=$(dg "$M" "$T/doc.pdf.inspect/index.md"); eq "artefactos .inspect → permitidos" "${R%%|*}" 0
rm -rf "$M/scripts/.venv-doc-tools" "$M/scripts/doclens.py"

echo ""
echo "══ Worktrees: $PASS ok, $FAIL fallos ══"
rm -rf "$T"
[ "$FAIL" -eq 0 ]
