FROM python:3.11-slim

LABEL maintainer="ScholarAgent"
LABEL description="Autonomous academic paper review agent"

# System dependencies for pymupdf
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY v2/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY v2/ /app/v2/
COPY .env.example /app/.env.example
COPY docs/ /app/docs/

# Create papers mount point
RUN mkdir -p /papers

# Default working directory for running the agent
WORKDIR /app

# Default command: show help
CMD ["python", "v2/main.py", "--help"]
