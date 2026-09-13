# Worker image: the API image plus the ML/OCR system libraries.
#
# Kept separate because these dependencies are large (OpenCV + Tesseract +
# Prophet's toolchain) and the API process never imports them. On a 1 GB
# instance that separation is the difference between fitting and not.

FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

ARG INSTALL_EXTRAS="auth"
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[${INSTALL_EXTRAS}]"

# ---------------------------------------------------------------------------

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Prophet/cmdstanpy and numpy each spawn thread pools sized to the host CPU
    # count; on a shared t3.micro that oversubscribes and thrashes.
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

# `upgrade` before `install` -- see backend.Dockerfile for the reasoning. Both
# images sit on the same base and inherit the same lagging OS packages, so a fix
# applied to only one of them would leave the worker carrying what the API had
# just cleared.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
        tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 frugal

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=frugal:frugal app ./app
# See backend.Dockerfile. This image is what the receipts job runs, and it runs
# it as `python -m scripts.run_jobs receipts`.
COPY --chown=frugal:frugal scripts ./scripts

USER frugal

# Concurrency 1: measured footprint is ~450 MB during a Prophet fit or an
# OpenCV pipeline, so a second concurrent worker on 1 GB is an OOM kill rather
# than a throughput gain (ADR-006).
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=1", "-Q", "default,ocr,ml"]
