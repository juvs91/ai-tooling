# Agente del simulador: lo más parecido a un Microsoft-hosted ubuntu-latest que
# importa para los scripts de pipeline (bash, git, jq, curl, python 3.12, docker
# CLI + buildx). `az` es el shim del simulador (solo `az acr …` de ci-acr.sh).
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq python3 python3-venv gnupg tar \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
 && rm -rf /var/lib/apt/lists/*

# UsePythonVersion@0 en Azure pone en PATH un python del toolcache donde
# `python -m pip install` funciona (no el python del sistema, que bajo PEP 668
# rechaza pip). El simulador lo emula con un venv escribible por cualquier uid.
RUN python3 -m venv /opt/hostedtoolcache/Python/3.12/x64 \
 && chmod -R a+rwX /opt/hostedtoolcache
ENV PATH=/opt/hostedtoolcache/Python/3.12/x64/bin:$PATH

COPY az /usr/local/bin/az
RUN chmod 0755 /usr/local/bin/az

ENV HOME=/tmp/agent-home
RUN mkdir -p /tmp/agent-home && chmod 1777 /tmp/agent-home \
 && git config --system --add safe.directory '*'

CMD ["sleep", "infinity"]
