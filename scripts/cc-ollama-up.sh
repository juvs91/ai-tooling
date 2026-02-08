cat > ~/bin/cc-ollama-up <<'SH'
#!/usr/bin/env bash
set -euo pipefail

# Wrapper para tu ollama-model (tagging reproducible para Claude Code proxy)
# Requiere: ~/bin/ollama-model (tu script)
# Uso típico:
#   cc-ollama-up make-all --base qwen2.5-coder:7b --prefix cc-qwen25c7b --ctx-chat 16384 --ctx-plan 32768
# Luego en tu .env local:
#   BIG_MODEL=cc-qwen25c7b:planning
#   SMALL_MODEL=cc-qwen25c7b:chat

usage() {
  cat <<'USAGE'
Usage:
  cc-ollama-up make-all --base <base_model> --prefix <tag_prefix> [opts]
  cc-ollama-up make     --base <base_model> --tag <full_tag> --profile <chat|planning|building> [opts]
  cc-ollama-up print-env --prefix <tag_prefix>

Opts:
  --ctx-chat N      (default: 16384)
  --ctx-plan N      (default: 32768)
  --ctx-build N     (default: 16384)
  --predict-chat N  (default: 384)
  --predict-plan N  (default: 900)
  --predict-build N (default: 900)
  --gpu N           (default: 0)  # CPU
  --force           overwrite Modelfiles

Examples:
  cc-ollama-up make-all --base qwen2.5-coder:7b --prefix cc-qwen25c7b
  cc-ollama-up print-env --prefix cc-qwen25c7b
USAGE
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing '$1' in PATH" >&2; exit 1; }
}

cmd="${1:-}"; shift || true

ctx_chat=16384
ctx_plan=32768
ctx_build=16384

pred_chat=384
pred_plan=900
pred_build=900

gpu=0
force=0

base=""
prefix=""
tag=""
profile=""

parse_common_opts() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --base) base="$2"; shift 2;;
      --prefix) prefix="$2"; shift 2;;
      --tag) tag="$2"; shift 2;;
      --profile) profile="$2"; shift 2;;

      --ctx-chat) ctx_chat="$2"; shift 2;;
      --ctx-plan) ctx_plan="$2"; shift 2;;
      --ctx-build) ctx_build="$2"; shift 2;;

      --predict-chat) pred_chat="$2"; shift 2;;
      --predict-plan) pred_plan="$2"; shift 2;;
      --predict-build) pred_build="$2"; shift 2;;

      --gpu) gpu="$2"; shift 2;;
      --force) force=1; shift 1;;

      -h|--help) usage; exit 0;;
      *) echo "Unknown arg: $1" >&2; usage; exit 1;;
    esac
  done
}

make_one() {
  local base="$1" tag="$2" profile="$3" ctx="$4" pred="$5"
  local force_flag=""
  [[ "$force" == "1" ]] && force_flag="--force"

  echo "[cc-ollama-up] creating: $tag (profile=$profile ctx=$ctx predict=$pred gpu=$gpu)"
  ollama-model make --base "$base" --tag "$tag" --profile "$profile" \
    --ctx "$ctx" --predict "$pred" --gpu "$gpu" $force_flag
}

case "$cmd" in
  make-all)
    need ollama
    need ollama-model
    parse_common_opts "$@"

    [[ -z "$base" || -z "$prefix" ]] && { echo "ERROR: --base and --prefix required" >&2; usage; exit 1; }

    make_one "$base" "${prefix}:chat"     "chat"     "$ctx_chat"  "$pred_chat"
    make_one "$base" "${prefix}:planning" "planning" "$ctx_plan"  "$pred_plan"
    make_one "$base" "${prefix}:building" "building" "$ctx_build" "$pred_build"

    echo
    echo "[cc-ollama-up] DONE. Tags created:"
    echo "  - ${prefix}:chat"
    echo "  - ${prefix}:planning"
    echo "  - ${prefix}:building"
    echo
    echo "[cc-ollama-up] Suggested .env:"
    echo "  OPENAI_BASE_URL=http://host.docker.internal:11434/v1"
    echo "  OPENAI_API_KEY=sk-local-dummy"
    echo "  SMALL_MODEL=${prefix}:chat"
    echo "  BIG_MODEL=${prefix}:planning"
    echo "  BUILDING_MODEL=${prefix}:building"
    ;;

  make)
    need ollama
    need ollama-model
    parse_common_opts "$@"

    [[ -z "$base" || -z "$tag" || -z "$profile" ]] && { echo "ERROR: --base --tag --profile required" >&2; usage; exit 1; }

    case "$profile" in
      chat)     make_one "$base" "$tag" "$profile" "$ctx_chat"  "$pred_chat" ;;
      planning) make_one "$base" "$tag" "$profile" "$ctx_plan"  "$pred_plan" ;;
      building) make_one "$base" "$tag" "$profile" "$ctx_build" "$pred_build" ;;
      *) echo "ERROR: invalid --profile=$profile" >&2; exit 1;;
    esac
    ;;

  print-env)
    parse_common_opts "$@"
    [[ -z "$prefix" ]] && { echo "ERROR: --prefix required" >&2; usage; exit 1; }
    cat <<EOF
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=sk-local-dummy
SMALL_MODEL=${prefix}:chat
BIG_MODEL=${prefix}:planning
BUILDING_MODEL=${prefix}:building
EOF
    ;;

  -h|--help|"")
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
SH

chmod +x ~/bin/cc-ollama-up
