#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-zai}"                  # zai|groq|gemini|ollama
SERVICE="${SERVICE_NAME:-proxy_cloud}"
ENV_DIR="${ENV_DIR:-profile-envs}"

mkdir -p "${ENV_DIR}"

ENVF="${ENV_DIR}/cloud.${PROFILE}.env"
OVR_DIR="cloud-provider-ymls"
mkdir -p "${OVR_DIR}"
OVR="${OVR_DIR}/docker-compose.${PROFILE}.override.yml"

write_override () {
  cat > "${OVR}" <<YAML
services:
  ${SERVICE}:
    env_file:
      - ./.env
      - ${ENVF}
YAML
}

case "${PROFILE}" in
  zai)
    cat > "${ENVF}" <<'ENV'
# Z.AI (OpenAI-compatible)
PREFERRED_PROVIDER=openai
OPENAI_BASE_URL=https://api.z.ai/api/paas/v4/
OPENAI_API_KEY=

# Ajusta a los modelos reales disponibles en tu cuenta Z.AI
BIG_MODEL=glm-4.7
SMALL_MODEL=glm-4.7
BUILDING_MODEL=glm-4.7

# Policy defaults (opcional)
TOOL_ALLOWLIST=
POLICY_NOTE_IN_SYSTEM=1
ENV
    write_override
    ;;
  groq)
    cat > "${ENVF}" <<'ENV'
# Groq (OpenAI-compatible)
PREFERRED_PROVIDER=openai
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=

# modelos ejemplo (ajústalos a los disponibles en tu cuenta Groq)
BIG_MODEL=llama-3.1-8b-instant
SMALL_MODEL=llama-3.1-8b-instant
BUILDING_MODEL=llama-3.1-8b-instant

# Policy defaults (opcional)
TOOL_ALLOWLIST=
POLICY_NOTE_IN_SYSTEM=1
ENV
    write_override
    ;;
  gemini)
    cat > "${ENVF}" <<'ENV'
# Gemini (Google) - tu server ya sabe leer GEMINI_API_KEY
PREFERRED_PROVIDER=google
GEMINI_API_KEY=

SMALL_MODEL=gemini-2.5-flash
BIG_MODEL=gemini-2.5-pro
BUILDING_MODEL=gemini-2.5-pro

# Policy defaults (opcional)
TOOL_ALLOWLIST=
POLICY_NOTE_IN_SYSTEM=1
ENV
    write_override
    ;;
  ollama)
    cat > "${ENVF}" <<'ENV'
# Ollama local (OpenAI-compatible)
PREFERRED_PROVIDER=openai
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=

SMALL_MODEL=cc-local:chat
BIG_MODEL=cc-local:big
BUILDING_MODEL=cc-local:build

# Policy defaults (opcional)
TOOL_ALLOWLIST=
POLICY_NOTE_IN_SYSTEM=1
ENV
    write_override
    ;;
  *)
    echo "[cc-proxy-init] unknown profile: ${PROFILE}" >&2
    exit 2
    ;;
esac

echo "[cc-proxy-init] wrote ${ENVF}"
echo "[cc-proxy-init] wrote ${OVR}"
echo
echo "Next:"
echo "  1) Fill the key in ${ENVF}"
echo "  2) docker compose -f docker-compose.yml -f ${OVR} up -d --build"
echo "     (or: ./scripts/cc-proxy-up.sh ${PROFILE} if you have it)"
