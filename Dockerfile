# ScholarAgent V2 — Production Dockerfile
# Minimal image: pymupdf ships pre-built wheels, no system deps needed

FROM python:3.11-slim

LABEL maintainer="ScholarAgent Team"
LABEL description="Autonomous academic paper review agent — cognitive architecture with 31 kill switches"
LABEL version="2.1"

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies (layer cached separately from source)
COPY v2/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY v2/ /app/v2/
COPY docs/ /app/docs/

# Copy env example
COPY .env.example /app/.env.example

# Create mount points
RUN mkdir -p /papers /app/v2/.workspace

# Non-root user for security
RUN useradd --create-home --shell /bin/bash scholar && \
    chown -R scholar:scholar /app /papers
USER scholar

# Default: show help
ENTRYPOINT ["python", "v2/main.py"]
CMD ["--help"]
