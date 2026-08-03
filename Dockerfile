# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# movie-narrator — multi-stage container image (v0.8.4)
#
# Targets
#   builder      internal — resolves dependencies and builds the wheel
#   runtime-gpu  CUDA-enabled image, intended for `docker run --gpus all`
#   runtime      DEFAULT — slim CPU image
#
# Stage ordering note: `runtime` is deliberately the LAST stage so that a
# plain `docker build .` (no --target) produces the lightweight CPU image.
# BuildKit (default since Docker 23) skips stages the target does not
# depend on, so the CUDA layers are never pulled for a CPU build.
#
# Usage
#   docker build -t movie-narrator:0.8.4 .
#   docker build -t movie-narrator:0.8.4-full --build-arg MN_EXTRAS=full .
#   docker build -t movie-narrator:0.8.4-gpu --target runtime-gpu .

# Python 3.12 is chosen deliberately: the `ml` extras (whisperx,
# faster-whisper, sentence-transformers) are pinned `python_version < "3.14"`,
# and 3.12 is the best-supported target across the PyTorch / CTranslate2
# wheel matrix. 3.13 still has patchy binary-wheel coverage for those
# packages, and 3.14 is excluded by the pins outright.
ARG PYTHON_VERSION=3.12

# Extras selection for the CPU image. Empty string installs the base
# package only. `media` (scenedetect) is the sensible default; `full`
# adds the heavy ml stack.
ARG MN_EXTRAS=media

# Extras for the GPU image — defaults to `full` because the whole point
# of the CUDA image is to run the ml stack on a device.
ARG MN_GPU_EXTRAS=full


# ─────────────────────────────────────────────────────────────
# Stage 1: builder — build the wheel and a self-contained venv
# ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ARG MN_EXTRAS

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build toolchain lives only in this stage — it never reaches runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

# ── Dependency layer ─────────────────────────────────────────
# Copy only the metadata needed to resolve dependencies, then install
# against a stub package. This layer is cached and only invalidated when
# pyproject.toml changes — editing source code does not re-resolve deps.
COPY pyproject.toml README.md ./
RUN mkdir -p src/movie_narrator \
    && : > src/movie_narrator/__init__.py \
    && pip install --upgrade pip setuptools wheel \
    && if [ -n "${MN_EXTRAS}" ]; then \
           pip install ".[${MN_EXTRAS}]"; \
       else \
           pip install "."; \
       fi

# ── Source layer ─────────────────────────────────────────────
# Real sources arrive last so that the expensive dependency layer above
# stays warm across ordinary code changes.
COPY src/ ./src/
RUN pip wheel --no-deps --wheel-dir /wheels . \
    && pip install --no-deps --force-reinstall /wheels/movie_narrator-*.whl


# ─────────────────────────────────────────────────────────────
# Stage 2: runtime-gpu — CUDA runtime for `--gpus all`
# ─────────────────────────────────────────────────────────────
# Built from the wheel produced above rather than from the 3.12 venv:
# the CUDA base ships Ubuntu's own Python, and copying a venv across
# interpreter versions would not work. Dependencies are resolved fresh
# for that interpreter (>=3.10 satisfies requires-python, and the `ml`
# pins `< 3.14` still hold).
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS runtime-gpu

ARG MN_GPU_EXTRAS

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# ffmpeg is a hard runtime requirement — moviepy and pydub shell out to it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /tmp/wheels
RUN WHEEL="$(ls /tmp/wheels/movie_narrator-*.whl)" \
    && python3 -m pip install --upgrade pip setuptools wheel \
    && if [ -n "${MN_GPU_EXTRAS}" ]; then \
           python3 -m pip install "${WHEEL}[${MN_GPU_EXTRAS}]"; \
       else \
           python3 -m pip install "${WHEEL}"; \
       fi \
    && rm -rf /tmp/wheels

# Non-root user with an explicit, stable UID/GID so that bind-mounted
# host directories get predictable ownership.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash app

WORKDIR /app
RUN mkdir -p /app/output /app/.mn_tasks && chown -R app:app /app

# Artifacts and task state must outlive the container.
VOLUME ["/app/output", "/app/.mn_tasks"]

USER app:app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python3", "-c", "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["mn"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--storage-dir", "/app/.mn_tasks"]


# ─────────────────────────────────────────────────────────────
# Stage 3: runtime — DEFAULT slim CPU image
# ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

# ffmpeg is a hard runtime requirement — moviepy and pydub shell out to it.
# No build toolchain is installed here; only the prebuilt venv is copied in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash app

COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv

WORKDIR /app
RUN mkdir -p /app/output /app/.mn_tasks && chown -R app:app /app

# Artifacts and task state must outlive the container.
VOLUME ["/app/output", "/app/.mn_tasks"]

USER app:app

EXPOSE 8765

# Uses the stdlib rather than curl so the runtime image stays free of
# extra apt packages. /health is exempt from X-API-Key auth (v0.6.1).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["mn"]
# Binding 0.0.0.0 is required for the port to be reachable from outside the
# container. `mn serve` refuses to start on a public interface without an
# API key, so set MN_API_KEY (or append --insecure at your own risk).
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--storage-dir", "/app/.mn_tasks"]
