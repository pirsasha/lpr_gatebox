FROM python:3.11-slim-bookworm

WORKDIR /app

# base deps (ВАЖНО: docker-compose v1 НЕ ставим)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
      zip \
    && rm -rf /var/lib/apt/lists/*

# --- docker CLI (static)
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
 && chmod +x /usr/local/bin/docker \
 && docker version || true

# --- docker compose v2 plugin (ЭТО КЛЮЧЕВО)
ARG DOCKER_COMPOSE_VERSION=v2.27.1
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose \
 && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
 && docker compose version || true

COPY updater/app.py /app/app.py

EXPOSE 9010
CMD ["python", "/app/app.py"]