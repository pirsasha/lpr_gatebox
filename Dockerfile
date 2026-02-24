# =========================================================
# LPR GateBox – unified Dockerfile (CPU stable)
# =========================================================

FROM python:3.11-slim AS base
WORKDIR /work

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*


# =========================================================
# deps layer (кэшируется)
# =========================================================
FROM base AS deps

COPY requirements.txt /work/requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -U pip setuptools wheel && \
    python -m pip install \
        torch==2.2.2 \
        torchvision==0.17.2 \
        --extra-index-url https://download.pytorch.org/whl/cpu && \
    python -m pip install -r /work/requirements.txt


# =========================================================
# UI build (vite → dist)
# =========================================================
FROM node:20-alpine AS ui_build
WORKDIR /ui

COPY ui/package.json ui/package-lock.json* /ui/
RUN npm ci

COPY ui/ /ui/
RUN npm run build

RUN test -f /ui/dist/index.html && test -d /ui/dist/assets


# =========================================================
# gatebox runtime
# =========================================================
FROM base AS gatebox
WORKDIR /app

# NEW: docker CLI (нужен CloudPub docker-backend'у; daemon НЕ ставим)
# Требует примонтированный /var/run/docker.sock (у тебя уже есть в compose).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /usr/local /usr/local

COPY app /app/app

# Чистим старую статику
RUN rm -rf /app/app/static && mkdir -p /app/app/static

# Копируем UI
COPY --from=ui_build /ui/dist/ /app/app/static/

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]


# =========================================================
# rtsp_worker runtime
# =========================================================
FROM base AS rtsp_worker

COPY --from=deps /usr/local /usr/local
COPY app /work/app

WORKDIR /work
CMD ["python", "-m", "app.rtsp_worker"]