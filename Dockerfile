# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

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

RUN useradd --system --create-home --uid 1000 bot
COPY --chown=bot:bot app ./app
COPY --chown=bot:bot alembic ./alembic
COPY --chown=bot:bot alembic.ini ./
COPY --chown=bot:bot config ./config
RUN mkdir -p /app/data /app/reports /app/uploads && chown -R bot:bot /app/data /app/reports /app/uploads

USER bot
EXPOSE 8080
CMD ["python", "-m", "app.presentation.bot.main"]
