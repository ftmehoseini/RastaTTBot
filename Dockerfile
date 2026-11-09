# syntax=docker/dockerfile:1
# Pin the base image by digest in .env (PYTHON_IMAGE=python:3.12-slim@sha256:...) so a new upstream
# build of python:3.12-slim doesn't invalidate the cache and force a full pip install on every deploy.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Servers in Iran often need a local PyPI mirror: --build-arg PIP_INDEX_URL=https://mirror/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# LibreOffice Calc (no GUI) recalculates the admins' attendance workbooks. Installed after pip so
# the cached pip layer is reused. A server that can't reach deb.debian.org sets APT_MIRROR in .env,
# e.g. APT_MIRROR=http://mirror.example.ir (the address that replaces http://deb.debian.org).
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 1000 bot
COPY --chown=bot:bot app ./app
COPY --chown=bot:bot alembic ./alembic
COPY --chown=bot:bot alembic.ini ./
COPY --chown=bot:bot config ./config
RUN mkdir -p /app/data /app/reports /app/uploads /app/workbooks \
    && chown -R bot:bot /app/data /app/reports /app/uploads /app/workbooks

USER bot
EXPOSE 8080
CMD ["python", "-m", "app.presentation.bot.main"]
