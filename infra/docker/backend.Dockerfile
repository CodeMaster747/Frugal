# Multi-stage: the builder carries compilers, the runtime does not.
# Base image is Python 3.11 regardless of the host interpreter, which is why
# all execution is containerised (docs/01-srs.md 5.1).

FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

# ML extras are opt-in: the API image should not carry OpenCV, Prophet, and
# their toolchains it will never import (ADR-006 -- Prophet loads in the worker
# only, and lazily even there).
ARG INSTALL_EXTRAS="auth"
# setuptools and wheel are upgraded alongside pip, not left at whatever version
# `python -m venv` seeded. They are build tooling that ends up in the runtime
# venv, and both had HIGH advisories against the seeded versions (setuptools
# CVE-2025-47273 path traversal, wheel CVE-2026-24049 code execution) that no
# scan caught, because a filesystem scan finds no Python lockfile to read and so
# reported the backend clean. Image scanning is now in CI for the same reason.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install ".[${INSTALL_EXTRAS}]"

# ---------------------------------------------------------------------------

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq for psycopg; curl for the container healthcheck.
# WITH_OCR is off for production images: the API process never imports OpenCV
# or Tesseract (OCR runs in the worker). The local dev image turns it on so the
# eval harness can run under `make eval`.
ARG WITH_OCR=false

# `upgrade` before `install`, because the base image lags Debian's security
# archive and the image scan gates on HIGH/CRITICAL with `ignore-unfixed: true`.
# That combination means a CVE *with* a published fix fails the build and cannot
# honestly be suppressed in .trivyignore -- suppression there is a claim the
# finding is unreachable, which is untrue when the answer is simply a newer
# package. python:3.11-slim-bookworm shipped libpcre2-8-0 10.42-1 against a
# patched 10.42-1+deb12u1 (CVE-2026-86145, CVE-2026-89161).
#
# Upgrading the whole set rather than naming that one package is deliberate: the
# next advisory against this base then needs no commit at all.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && if [ "$WITH_OCR" = "true" ]; then \
         apt-get install -y --no-install-recommends \
           tesseract-ocr tesseract-ocr-eng libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1; \
       fi \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 frugal

COPY --from=builder /opt/venv /opt/venv

# Strip the base image's own Python tooling.
#
# `python:3.11-slim` ships pip, setuptools, and wheel in /usr/local, and
# setuptools vendors copies of wheel and jaraco.context inside itself. The
# application runs entirely from /opt/venv (see PATH above) and never touches
# any of it -- but a vulnerability scanner reads the filesystem, not the PATH,
# so those unused copies were the whole of this image's HIGH findings.
#
# Removing them is not just scanner hygiene: a runtime container that can
# install packages is a more useful foothold than one that cannot.
RUN /usr/local/bin/python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true \
    && rm -rf /usr/local/lib/python3.11/site-packages/setuptools* \
              /usr/local/lib/python3.11/site-packages/pkg_resources* \
              /usr/local/lib/python3.11/site-packages/wheel* \
              /usr/local/lib/python3.11/site-packages/pip* \
              /usr/local/lib/python3.11/ensurepip

# pip inside the venv, too -- but only for images that will not need it.
#
# pip vendors its own copies of msgpack, urllib3, and others, and a scanner
# reports those as findings even though nothing imports them: production never
# runs pip. The local development image keeps it, because `make lint` and
# `make test` execute inside that container and installing a tool there is
# routine.
ARG KEEP_PIP=false
RUN if [ "$KEEP_PIP" != "true" ]; then \
      rm -rf /opt/venv/lib/python3.11/site-packages/pip \
             /opt/venv/lib/python3.11/site-packages/pip-*.dist-info \
             /opt/venv/bin/pip /opt/venv/bin/pip3*; \
    fi

WORKDIR /app
COPY --chown=frugal:frugal alembic.ini ./
COPY --chown=frugal:frugal alembic ./alembic
COPY --chown=frugal:frugal app ./app
# Maintenance entry points, run as `python -m scripts.X`. Without this the
# scheduled jobs exit `ModuleNotFoundError: No module named 'scripts'` -- the
# image has carried `alembic/` for the same reason since migrations became a
# deploy step, and `scripts/run_jobs.py` is now how background work runs at all.
COPY --chown=frugal:frugal scripts ./scripts

USER frugal
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
