FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

RUN rm -f \
        /etc/apt/sources.list.d/yarn.list \
        /etc/apt/sources.list.d/yarnpkg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.0 \
    /uv \
    /uvx \
    /usr/local/bin/