FROM python:3.13-slim

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Node is required at runtime: the segmentation feature's Agent SDK drives the bundled Claude Code CLI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
WORKDIR /app/src

RUN uv sync --frozen --no-cache

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=UTF-8

# Run from /app/src so bare absolute imports (server, routes.*, services.*) resolve.
CMD ["uv", "run", "gunicorn", "--config", "utils/gunicorn_utils/gunicorn_config.py", "server:app"]
