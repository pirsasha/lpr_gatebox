# =========================================================
# Файл: updater/Dockerfile
# Проект: LPR GateBox
# Версия: v0.3.6-updater-dockercli-static
#
# Что сделано:
# - FIX: убрали устаревший docker.io (Debian) -> ставим свежий docker CLI (static)
# - docker-compose (standalone v1) оставляем из Debian (как и было)
# - zip нужен для /report
# =========================================================

FROM python:3.11-slim-bookworm

WORKDIR /app

# base deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
      docker-compose \
      zip \
    && rm -rf /var/lib/apt/lists/*

# --- NEW: docker CLI (static, always new enough for modern daemons)
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
 && chmod +x /usr/local/bin/docker \
 && docker version || true

COPY updater/app.py /app/app.py

EXPOSE 9010
CMD ["python", "/app/app.py"]