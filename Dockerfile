# ── Base: NVIDIA CUDA 12.4 + cuDNN on Ubuntu 22.04 ───────────────────────────
# Matches the host driver (CUDA 13.0, backward compatible) and the cu126
# paddlepaddle-gpu wheels installed below (same CUDA 12.x major ABI).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# System deps required by PyMuPDF and OpenCV (pulled in by paddlex[ocr])
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-dev \
    python3-pip \
    python3.10-venv \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.10 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.10 /usr/bin/python

WORKDIR /app

# ── Application dependencies (FastAPI, PaddleOCR, etc.) ──────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── PaddlePaddle GPU build for PaddleOCR-VL ───────────────────────────────────
# Installed LAST, pinned to match the CPU paddlepaddle version this project
# was validated against, with its own --extra-index-url so pip resolves its
# exact nvidia-*-cu12 pins instead of silently mixing them with another
# package's CUDA wheels.
RUN pip install --no-cache-dir --retries 10 --timeout 120 paddlepaddle-gpu==3.3.1 \
    --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/

# ── Application code ──────────────────────────────────────────────────────────
COPY api/ ./api/
COPY app_db/ ./app_db/
COPY config/ ./config/
COPY engine/ ./engine/
COPY frontend/ ./frontend/
COPY models/ ./models/
COPY scripts/ ./scripts/
COPY services/ ./services/
COPY utils/ ./utils/
COPY app.py .

# storage/ is bind-mounted by docker-compose; keep the tree present for local
# `docker run` usage without compose.
RUN mkdir -p storage/input storage/pages storage/json storage/output storage/tmp storage/db

# No build-time model pre-fetch: importing paddle's GPU-linked core requires
# libcuda.so.1, which isn't present in a plain `docker build` sandbox without
# GPU passthrough. PaddleOCR-VL's model files download on first real request
# instead, inside the running container where GPU passthrough is present.
# The docker-compose model_cache volume persists that download across restarts.

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
