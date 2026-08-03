# ============================================================================
# Stage 1: Builder - Install dependencies and compile bytecode
# ============================================================================
FROM python:3.12-slim-bookworm AS builder

# Copy uv from official image (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation and optimize for container builds
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies first (cached layer - only rebuilds if lock files change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-editable --no-dev

# Copy application code
COPY app /app/app
COPY scripts /app/scripts
COPY pyproject.toml uv.lock /app/

# Sync the project itself (separate layer for better caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-dev

# ============================================================================
# Stage 2: Production - Minimal runtime image
# ============================================================================
FROM python:3.12-slim-bookworm AS production

# Labels for better container management
LABEL org.opencontainers.image.title="Portal"

# Copy uv for runtime (pinned version)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install gosu for dropping privileges in entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# Runtime environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create non-root user first (before copying files)
RUN groupadd -r -g 1000 appuser && \
    useradd -r -u 1000 -g appuser appuser

# Create runtime directories with proper permissions
# uploads/sessions - session-specific uploaded files
# uploads/global - global admin uploaded files (e.g., prerequisites)
RUN mkdir -p uploads/sessions uploads/global exports logs backups && \
    chown -R appuser:appuser /app

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy application code from builder
COPY --from=builder --chown=appuser:appuser /app/app /app/app
COPY --from=builder --chown=appuser:appuser /app/scripts /app/scripts
COPY --from=builder --chown=appuser:appuser /app/pyproject.toml /app/uv.lock /app/

# Make entrypoint executable
RUN chmod +x /app/scripts/entrypoint.sh

# Expose port
EXPOSE 23090

# Use entrypoint to handle permissions and drop privileges
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
# Bind $PORT when the host assigns one (Railway, Render, Fly), else the local default
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-23090}"]
