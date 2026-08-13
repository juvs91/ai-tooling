# pg-combo: Postgres 16 + Citus (columnar, vía apt oficial) + Apache AGE (grafo, build desde
# fuente). Ambas son extensiones normales (shared_preload_libraries), a diferencia de Hydra
# Columnar cuyo build real usa pgxman + artefactos internos de CI no reproducibles a mano
# (ver poc-pg-extensions/README.md).
# Citus solo publica paquetes apt para x86_64 — en Apple Silicon (arm64) forzamos
# emulación amd64 (Rosetta/QEMU vía Docker Desktop) solo para este contenedor.
# -bookworm explícito: el tag "postgres:16" resolvió a Debian trixie, donde el script de
# instalación de Citus detecta mal la versión de Postgres del sistema (confunde con un
# "postgresql-18" del repo base de Debian) y no encuentra el paquete postgresql-16-citus-*.
FROM --platform=linux/amd64 postgres:16-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        libreadline-dev \
        zlib1g-dev \
        flex \
        bison \
        git \
        postgresql-server-dev-16 \
    && curl https://install.citusdata.com/community/deb.sh | bash \
    && apt-get install -y --no-install-recommends postgresql-16-citus-13.0 \
    && git clone --depth 1 --branch PG16/v1.6.0-rc0 https://github.com/apache/age.git /tmp/age \
    && cd /tmp/age \
    && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install \
    && cd / \
    && rm -rf /tmp/age \
    && apt-get purge -y --auto-remove build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY postgresql.conf /usr/share/postgresql/16/postgresql.conf.sample
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql
