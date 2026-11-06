#!/usr/bin/env bash
# tests/gitops/release-tests.sh — release.sh tag y promote (ADR-0082) con repos git reales: un origin bare, un
# clon con .gitops-env y proyectos en la raíz, worktrees, un checkout de CI desprendido y un repo con trunk main
# sin .gitops-env. H1..H9 son los nueve hallazgos de ADR-0082; R* lo que no debe romperse.
#   bash tests/gitops/release-tests.sh [<release.sh>]   (default: scripts/release.sh de este repo)
set -u
AI_TOOLING="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REL="${1:-$AI_TOOLING/scripts/release.sh}" CI="$AI_TOOLING/scripts/ci-image.sh"
base="$(mktemp -d)"; trap 'rm -rf "$base"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
unset GITOPS_REMOTE GITOPS_TRUNK_BRANCH GITOPS_PROJECTS_DIR GITOPS_PROJECT_MAP
pass=0 fail=0
res() { if [ "$1" = ok ]; then pass=$((pass+1)); echo "PASS  $2"; else fail=$((fail+1)); echo "FAIL  $2${3:+ — $3}"; fi; }
REALGIT="$(command -v git)"

setup() {
  cd "$base" || exit 1
  rm -rf origin.git w wt shim
  "$REALGIT" init -q --bare -b master origin.git
  "$REALGIT" clone -q origin.git w 2>/dev/null; cd w || exit 1
  "$REALGIT" checkout -q -b master 2>/dev/null || true
  mkdir -p scripts proj other
  cp "$REL" scripts/release.sh; cp "$CI" scripts/ci-image.sh; cp "$AI_TOOLING/scripts/ci-lib.sh" "$AI_TOOLING/scripts/deps.py" scripts/ 2>/dev/null || true
  # this repo's .gitops-env, with the projects at the root
  printf '# comment\nGITOPS_TRUNK_BRANCH=master\nGITOPS_REMOTE=origin\nGITOPS_PROJECTS_DIR=.\nGITOPS_PROJECT_MAP=other:other\n' > .gitops-env
  echo "1.2.3" > proj/VERSION; echo "0.1.0" > other/VERSION
  "$REALGIT" add -A && "$REALGIT" commit -qm init && "$REALGIT" push -q origin master 2>/dev/null
  MASTER="$("$REALGIT" rev-parse HEAD)"
}
rel() { bash scripts/release.sh "$@" >"$base/out" 2>&1; }          # never sources .gitops-env
tags() { "$REALGIT" ls-remote --tags origin | sed 's#.*refs/tags/##; s/\^{}$//' | sort -u; }
at() { "$REALGIT" ls-remote origin "refs/tags/$1^{}" | cut -f1; }     # commit a remote annotated tag points to
side_push() {  # another person pushes a commit to master
  "$REALGIT" clone -q "$base/origin.git" "$base/o2" 2>/dev/null
  (cd "$base/o2" && "$REALGIT" checkout -q master && echo "$1" > "s$1" && "$REALGIT" add -A && "$REALGIT" commit -qm "$1" && "$REALGIT" push -q origin master)
  rm -rf "$base/o2"
}

# H1 promote of a project that does not exist
setup; rel promote nope dev; rel promote nope 1.0.0 dev
tags | grep -q '^nope@' && res ko "H1 promote refuses a project that does not exist" "created $(tags | grep nope | tr '\n' ' ')" || res ok "H1 promote refuses a project that does not exist"

# H2 promote dev from a feature branch tags master, never the branch commit
setup; "$REALGIT" checkout -q -b feat; echo f > f; "$REALGIT" add f; "$REALGIT" commit -qm f; "$REALGIT" push -q origin feat 2>/dev/null; rel promote proj dev
[ "$(at proj@1.2.3-dev.1)" = "$MASTER" ] && res ok "H2 promote dev from a branch tags origin/master" || res ko "H2 promote dev from a branch tags origin/master" "tag at $(at proj@1.2.3-dev.1 | cut -c1-7), master ${MASTER:0:7}; $(tail -1 "$base/out")"

# H2b unpushed commit on local master is never tagged
setup; echo u > u; "$REALGIT" add u; "$REALGIT" commit -qm u; rel promote proj dev
[ "$(at proj@1.2.3-dev.1)" = "$MASTER" ] && res ok "H2b an unpushed local commit is never tagged" || res ko "H2b an unpushed local commit is never tagged" "$(tail -1 "$base/out")"

# H3 promote with a version that is not the manifest's
setup; rel promote proj 7.7.7 dev
tags | grep -q '7.7.7' && res ko "H3 promote refuses a version that is not the manifest's" "created proj@7.7.7-dev.1" || res ok "H3 promote refuses a version that is not the manifest's"

# H3b promote without a version reads it from the manifest of origin/master
setup; rel promote proj dev
tags | grep -qx 'proj@1.2.3-dev.1' && res ok "H3b promote <p> dev reads the version of origin/master's manifest" || res ko "H3b promote <p> dev reads the version" "$(tail -1 "$base/out")"

# H3c a bump only local (not in origin) does not change the tag's version
setup; echo 2.0.0 > proj/VERSION; "$REALGIT" commit -qam b; rel promote proj dev
tags | grep -qx 'proj@1.2.3-dev.1' && res ok "H3c the version is origin/master's, not a local bump" || res ko "H3c version from origin" "$(tags | tr '\n' ' ')"

# H4 tag and promote rc from a worktree on a branch (master is in the main checkout)
setup; "$REALGIT" worktree add -q -b work "$base/wt" 2>/dev/null; cd "$base/wt" || exit 1; rel promote proj rc; o1="$(tail -1 "$base/out")"; rel tag proj; o2="$(tail -1 "$base/out")"; cd "$base/w" || exit 1
if [ "$(at proj@1.2.3-rc.1)" = "$MASTER" ] && [ "$(at proj@1.2.3)" = "$MASTER" ]; then res ok "H4 tag and promote rc work from a worktree and tag origin/master"; else res ko "H4 release from a worktree" "$o1 | $o2"; fi

# H5 .gitops-env is read without sourcing it, and the environment wins over it
setup; rel tag proj
tags | grep -qx 'proj@1.2.3' && res ok "H5 .gitops-env is read by release.sh itself" || res ko "H5 .gitops-env read by itself" "$(tail -1 "$base/out")"
setup; GITOPS_PROJECTS_DIR=nowhere bash scripts/release.sh promote proj dev >"$base/out" 2>&1
tags | grep -q proj@ && res ko "H5b the environment wins over .gitops-env" "ignored GITOPS_PROJECTS_DIR=nowhere" || res ok "H5b the environment wins over .gitops-env"

# H5c .gitops-env is data, never run
setup; printf 'GITOPS_X=$(touch %s/pwned)\ntouch %s/pwned2\n' "$base" "$base" >> .gitops-env; "$REALGIT" commit -qam e; "$REALGIT" push -q origin master; rel versions
[ ! -e "$base/pwned" ] && [ ! -e "$base/pwned2" ] && res ok "H5c .gitops-env is read as data, never executed" || res ko "H5c .gitops-env executed"

# H6 a project at the root that is not in the map (new_project.sh adds none)
setup; mkdir -p newp; echo 0.0.1 > newp/VERSION; "$REALGIT" add -A; "$REALGIT" commit -qm n; "$REALGIT" push -q origin master; rel promote newp dev
tags | grep -qx 'newp@0.0.1-dev.1' && res ok "H6 a root project missing from the map is found (GITOPS_PROJECTS_DIR=.)" || res ko "H6 root project not in the map" "$(tail -1 "$base/out")"

# H7 VERSION with CRLF and trailing spaces
setup; printf '1.2.3 \r\n' > proj/VERSION; "$REALGIT" commit -qam crlf; "$REALGIT" push -q origin master; rel tag proj
tags | grep -qx 'proj@1.2.3' && res ok "H7 a VERSION with CRLF or spaces works" || res ko "H7 CRLF VERSION" "$(tail -1 "$base/out")"

# H8 local master behind origin: no 'sync' (rebase) advice, and the tag is origin's head
setup; side_push x; NEW="$("$REALGIT" ls-remote origin refs/heads/master | cut -f1)"; rel tag proj
if grep -q 'release.sh sync' "$base/out"; then res ko "H8 never advises release.sh sync" "$(tail -1 "$base/out")"; elif [ "$(at proj@1.2.3)" = "$NEW" ]; then res ok "H8 master behind origin: tags origin's head, no sync advice"; else res ko "H8 behind origin" "$(tail -1 "$base/out")"; fi

# H9 success message
setup; rel tag proj
grep -qi 'bitbucket' "$base/out" && res ko "H9 no Bitbucket in the messages" || res ok "H9 no Bitbucket in the messages"

# R1 tag: annotated, only its own tag pushed, parse-tag → prod
setup; "$REALGIT" tag local-only; rel tag proj
if tags | grep -qx 'proj@1.2.3' && ! tags | grep -q local-only && [ "$("$REALGIT" cat-file -t proj@1.2.3)" = tag ] && bash scripts/ci-image.sh parse-tag proj@1.2.3 2>&1 | grep -q 'env=prod'; then res ok "R1 tag: annotated, only its tag pushed, parse-tag → prod"; else res ko "R1 tag" "$(tail -1 "$base/out")"; fi

# R2 tags are immutable, also when the tag exists only on origin
setup; rel tag proj; "$REALGIT" tag -d proj@1.2.3 >/dev/null; rel tag proj
grep -q 'ya existe' "$base/out" && res ok "R2 an existing tag (even only on origin) is refused" || res ko "R2 immutable" "$(tail -1 "$base/out")"

# R3 expected version must match
setup; rel tag proj 9.9.9
grep -q 'esperabas 9.9.9' "$base/out" && ! tags | grep -q proj@ && res ok "R3 tag <p> <ver> refuses a version that is not the manifest's" || res ko "R3 expected version" "$(tail -1 "$base/out")"

# R4 manifest not X.Y.Z / no manifest
setup; echo 1.2 > proj/VERSION; "$REALGIT" commit -qam v; "$REALGIT" push -q origin master; rel tag proj; o="$(cat "$base/out")"; rel promote proj dev
echo "$o" | grep -q 'versión de release limpia' && grep -q 'versión de release limpia' "$base/out" && ! tags | grep -q proj@ && res ok "R4 a manifest that is not X.Y.Z is refused (tag and promote)" || res ko "R4 non-semver" "$(tail -1 "$base/out")"
setup; mkdir -p nomani; touch nomani/.keep; "$REALGIT" add -A; "$REALGIT" commit -qm e; "$REALGIT" push -q origin master; rel promote nomani dev
grep -q 'manifiesto' "$base/out" && ! tags | grep -q nomani && res ok "R4b a project without manifest is refused" || res ko "R4b no manifest" "$(tail -1 "$base/out")"

# R5 pending hotfix blocks tag
setup; "$REALGIT" checkout -q -b hf; echo h > h; "$REALGIT" add h; "$REALGIT" commit -qm hf; "$REALGIT" tag -a proj@1.2.2-hotfix.1 -m h; "$REALGIT" push -q origin proj@1.2.2-hotfix.1; "$REALGIT" checkout -q master; rel tag proj
grep -q 'cherry-pick pendiente' "$base/out" && ! tags | grep -qx proj@1.2.3 && res ok "R5 tag blocks while a hotfix is not in master" || res ko "R5 hotfix" "$(tail -1 "$base/out")"

# R6 numbering: numeric max + 1, per project and env, from origin's tags
setup; for n in 1 2 10; do "$REALGIT" tag -a "proj@1.2.3-dev.$n" -m x; "$REALGIT" push -q origin "proj@1.2.3-dev.$n"; "$REALGIT" tag -d "proj@1.2.3-dev.$n" >/dev/null; done
"$REALGIT" tag -a 'proj2@1.2.3-dev.50' -m x; "$REALGIT" push -q origin 'proj2@1.2.3-dev.50'; "$REALGIT" tag -a 'proj@1.2.3-rc.7' -m x; "$REALGIT" push -q origin 'proj@1.2.3-rc.7'
rel promote proj dev
tags | grep -qx 'proj@1.2.3-dev.11' && res ok "R6 N = numeric max of origin's tags + 1, not mixing projects or envs" || res ko "R6 numbering" "$(tail -1 "$base/out"); $(tags | tr '\n' ' ')"

# R7 race: someone pushes the same N between our count and our push
setup; mkdir -p "$base/shim"; cat > "$base/shim/git" <<EOF
#!/bin/sh
case " \$* " in *" push "*"proj@1.2.3-dev.1"*)
  if [ ! -e "$base/raced" ]; then : > "$base/raced"
    "$REALGIT" clone -q "$base/origin.git" "$base/r2" 2>/dev/null
    (cd "$base/r2" && "$REALGIT" tag -a proj@1.2.3-dev.1 -m other && "$REALGIT" push -q origin proj@1.2.3-dev.1)
  fi ;;
esac
exec "$REALGIT" "\$@"
EOF
chmod +x "$base/shim/git"; rm -f "$base/raced"; PATH="$base/shim:$PATH" bash scripts/release.sh promote proj dev >"$base/out" 2>&1; rm -rf "$base/r2"
tags | grep -qx 'proj@1.2.3-dev.2' && res ok "R7 a race on the same N retries with the next one" || res ko "R7 race" "$(tail -2 "$base/out" | tr '\n' ' ')"

# R8 rollback: --at an older master commit; a commit off master is refused
setup; OLD="$MASTER"; side_push y; rel promote proj dev --at "$OLD"; o="$(at proj@1.2.3-dev.1)"
"$REALGIT" checkout -q -b off; echo o > o; "$REALGIT" add o; "$REALGIT" commit -qm o; OFF="$("$REALGIT" rev-parse HEAD)"; rel promote proj dev --at "$OFF"
if [ "$o" = "$OLD" ] && grep -q 'no está en origin/master' "$base/out"; then res ok "R8 --at tags an older master commit and refuses one off master"; else res ko "R8 --at" "at=$o; $(tail -1 "$base/out")"; fi

# R9 promote prod refused; rc and dev parse to dev
setup; rel promote proj prod; o="$(cat "$base/out")"; rel promote proj rc
echo "$o" | grep -q 'ambiente inválido' && bash scripts/ci-image.sh parse-tag proj@1.2.3-rc.1 2>&1 | grep -q 'env=' && res ok "R9 promote refuses prod; rc tags parse" || res ko "R9" "$o"

# R10 a dirty tree does not matter (the tag is origin/master's commit)
setup; echo dirty >> proj/VERSION; rel promote proj dev
[ "$(at proj@1.2.3-dev.1)" = "$MASTER" ] && res ok "R10 a dirty working tree does not change what is tagged" || res ko "R10 dirty tree" "$(tail -1 "$base/out")"

# R11 CI checkout: detached HEAD on the trunk commit (Bitbucket tag-multi-prod calls release.sh tag there)
setup; "$REALGIT" checkout -q --detach; rel tag proj
[ "$(at proj@1.2.3)" = "$MASTER" ] && res ok "R11 tag from a detached CI checkout" || res ko "R11 detached CI checkout" "$(tail -1 "$base/out")"

# R12 a repo with trunk main, projects/ and no .gitops-env (the defaults)
cd "$base" && rm -rf origin.git w && "$REALGIT" init -q --bare -b main origin.git && "$REALGIT" clone -q origin.git w 2>/dev/null && cd w || exit 1
"$REALGIT" checkout -q -b main 2>/dev/null || true
mkdir -p scripts projects/svc; cp "$REL" scripts/release.sh; echo 3.0.0 > projects/svc/VERSION
"$REALGIT" add -A && "$REALGIT" commit -qm init && "$REALGIT" push -q origin main 2>/dev/null; MAIN="$("$REALGIT" rev-parse HEAD)"
rel promote svc dev; rel tag svc
[ "$(at svc@3.0.0-dev.1)" = "$MAIN" ] && [ "$(at svc@3.0.0)" = "$MAIN" ] && res ok "R12 defaults: trunk main, projects/<svc>, no .gitops-env" || res ko "R12 defaults" "$(tail -1 "$base/out")"

# R13 manifests: pyproject.toml and package.json (version read from the commit)
setup; mkdir -p py js; printf '[project]\nname = "py"\nversion = "4.5.6"\n' > py/pyproject.toml; printf '{"name": "js", "version": "7.8.9"}\n' > js/package.json
"$REALGIT" add -A; "$REALGIT" commit -qm m; "$REALGIT" push -q origin master; rel promote py dev; o1="$(tail -1 "$base/out")"; rel tag js
tags | grep -qx 'py@4.5.6-dev.1' && tags | grep -qx 'js@7.8.9' && res ok "R13 pyproject.toml and package.json versions" || res ko "R13 manifests" "$o1 | $(tail -1 "$base/out")"

# R14 bump writes the manifest (VERSION and pyproject.toml), unchanged
setup; mkdir -p py; printf '[project]\nname = "py"\nversion = "1.0.0"\n' > py/pyproject.toml; rel bump proj 1.3.0; rel bump py 1.1.0
[ "$(cat proj/VERSION)" = 1.3.0 ] && grep -q '^version = "1.1.0"' py/pyproject.toml && res ok "R14 bump writes VERSION and pyproject.toml" || res ko "R14 bump" "$(tail -1 "$base/out")"

# R15 hotfix: branch from the prod tag; check reports it pending; cherry absorbs it into master; check clean
setup; rel tag proj; rel hotfix proj 1.2.3 fix1; br="$("$REALGIT" rev-parse --abbrev-ref HEAD)"
echo fix > fix.txt; "$REALGIT" add fix.txt; "$REALGIT" commit -qm "fix(proj): x"; "$REALGIT" tag -a proj@1.2.3-hotfix.1 -m h; "$REALGIT" push -q origin proj@1.2.3-hotfix.1
rel check proj; pending="$(grep -c PENDIENTE "$base/out")"
"$REALGIT" checkout -q master; rel cherry proj 1.2.3; o="$(cat "$base/out")"; rel check proj
if [ "$br" = hotfix/proj/fix1 ] && [ "$pending" = 1 ] && "$REALGIT" ls-remote origin refs/heads/master | grep -q "$("$REALGIT" rev-parse HEAD)" && grep -q 'sin hotfixes pendientes' "$base/out"; then
  res ok "R15 hotfix → check (pending) → cherry → check (clean)"
else res ko "R15 hotfix flow" "branch=$br pending=$pending; $(echo "$o" | tail -2 | tr '\n' ' ')"; fi

# R16 versions lists every env
setup; rel tag proj; rel promote proj dev; rel promote proj rc; rel versions proj
grep -q 'proj@1.2.3-dev.1 .*DEV' "$base/out" && grep -q 'proj@1.2.3-rc.1 .*QA' "$base/out" && grep -q 'proj@1.2.3 .*PROD' "$base/out" && res ok "R16 versions" || res ko "R16 versions" "$(cat "$base/out")"

# R17 work / expand (sparse checkout) read the map from .gitops-env
setup; rel work other; s1="$("$REALGIT" sparse-checkout list 2>/dev/null | tr '\n' ' ')"; rel expand; s2="$("$REALGIT" config core.sparseCheckout || echo false)"
case "$s1" in *other*) [ "$s2" != true ] && res ok "R17 work other → sparse set with other; expand → full" || res ko "R17 expand" "$s2";; *) res ko "R17 work" "$s1 / $(tail -1 "$base/out")";; esac

# R18 GITOPS_PROJECT_RESOLVER (ADR-0083): a nested project's folder comes from a repo script, not from the map;
# an unknown name falls through to GITOPS_PROJECTS_DIR, and a resolver that is a command (not a path) is refused
setup; mkdir -p prod/deep; echo "2.0.0" > prod/deep/VERSION
printf '#!/usr/bin/env sh\ncase "$1" in deep|dp) echo prod/deep ;; *) exit 1 ;; esac\n' > scripts/resolver.sh
echo "GITOPS_PROJECT_RESOLVER=scripts/resolver.sh" >> .gitops-env
"$REALGIT" add -A && "$REALGIT" commit -qm nested && "$REALGIT" push -q origin master 2>/dev/null
rel promote deep dev; rel promote dp dev; rel promote proj dev
tags | grep -qx 'deep@2.0.0-dev.1' && tags | grep -qx 'dp@2.0.0-dev.1' && tags | grep -qx 'proj@1.2.3-dev.1' \
  && res ok "R18 resolver: nested folder by name and by alias; others fall through to the projects dir" \
  || res ko "R18 resolver" "$(tags | tr '\n' ' '); $(tail -1 "$base/out")"
setup; echo 'GITOPS_PROJECT_RESOLVER=scripts/release.sh; touch pwned' >> .gitops-env; rel promote proj dev
[ ! -e pwned ] && grep -q 'GITOPS_PROJECT_RESOLVER debe ser' "$base/out" && res ok "R18b a resolver that is not a plain repo path is refused, never run" \
  || res ko "R18b resolver as a command" "$(tail -1 "$base/out")"

echo "--- $pass passed, $fail failed"
[ "$fail" = 0 ]
