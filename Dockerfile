# ScholarAgent V2 — Production Dockerfile
# Multi-stage build for minimal image size

FROM python:3.11-slim AS base

LABEL maintainer="ScholarAgent Team"
LABEL description="Autonomous academic paper review agent — cognitive architecture with 31 kill switches"
LABEL version="2.1"

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# System dependencies for pymupdf (PDF parsing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libmupdf-dev \
        libfreetype6 \
        libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Install Python dependencies (layer cached separately from source)
COPY v2/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY v2/ /app/v2/
COPY .env.example /app/.env.example
COPY docs/ /app/docs/

# Create mount points
RUN mkdir -p /papers /app/v2/.workspace

# Health check: verify critical imports work
RUN python -c "\
import sys; sys.path.insert(0, '/app/v2'); \
from core.godel_config import log_config_status; \
print('Docker health check: imports OK')" \
    || (echo 'FATAL: import check failed' && exit 1)

# Default working directory
WORKDIR /app

# Non-root user for security
RUN useradd --create-home --shell /bin/bash scholar && \
    chown -R scholar:scholar /app /papers
USER scholar

# Default: show help
ENTRYPOINT ["python", "v2/main.py"]
CMD ["--help"]
