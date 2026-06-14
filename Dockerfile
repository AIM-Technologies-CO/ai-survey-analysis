FROM python:3.13-slim

# uv (Python package/venv manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Node + the Claude Code CLI. The segmentation Agent SDK shells out to the `claude`
# binary (claude_agent_sdk does shutil.which("claude")), so the CLI MUST be on PATH
# inside the image. Node 22 + CLI pinned to the version proven on the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@2.1.177 \
    && npm cache clean --force \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching the host's dev_user (uid/gid 1002) so the bind-mounted
# volumes (runs/uploads/logs/shared/exports/tracking) stay writable as that owner.
RUN groupadd -g 1002 app \
    && useradd -m -u 1002 -g 1002 app \
    && mkdir -p /app \
    && chown 1002:1002 /app

COPY --chown=1002:1002 . /app

# Put the project venv first on PATH. This effectively "activates" it for EVERY
# subprocess, including the analysis scripts the agent runs via `python work/*.py`
# (which need pandas/numpy/matplotlib/scikit-learn from the venv, not system python).
ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/app \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8

USER app
WORKDIR /app/src

# Runtime dirs (gunicorn opens logs/ before app code runs). These are normally
# bind-mounted by compose; creating them keeps the image runnable on its own too.
RUN mkdir -p /app/logs /app/runs /app/uploads /app/exports /app/tracking /app/shared

# uv discovers /app/pyproject.toml and builds /app/.venv from the frozen lockfile.
RUN uv sync --frozen --no-cache

# Run from /app/src so bare absolute imports (server, routes.*, services.*) resolve.
# gunicorn binds :$PORT_NUMBER (see utils/gunicorn_utils/gunicorn_config.py).
CMD ["gunicorn", "--config", "utils/gunicorn_utils/gunicorn_config.py", "server:app"]
