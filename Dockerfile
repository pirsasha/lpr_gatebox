# syntax=docker/dockerfile:1.6

FROM python:3.11-slim AS base
WORKDIR /work

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0

# системные зависимости (opencv-headless обычно ок без libgl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ---- deps layer (кэшируется) ----
FROM base AS deps

# Копируем ТОЛЬКО requirements — чтобы правки кода не ломали кэш
COPY requirements.txt /work/requirements.txt

# 1) CPU torch/torchvision отдельно (чтобы не получить cu12)
# 2) остальные зависимости с deps (urllib3 подтянется для requests)
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -U pip setuptools wheel && \
    python -m pip install --index-url https://download.pytorch.org/whl/cpu \
      torch==2.2.2 torchvision==0.17.2 && \
    python -m pip install -r /work/requirements.txt

# ---- gatebox runtime ----
FROM base AS gatebox
COPY --from=deps /usr/local /usr/local
COPY app /work/app
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# ---- rtsp_worker runtime ----
FROM base AS rtsp_worker
COPY --from=deps /usr/local /usr/local
COPY app /work/app
CMD ["python", "app/rtsp_worker.py"]
