FROM python:3.12-slim

# System dependencies needed for tools and building native extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        unzip \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Tier 1 tools: pre-installed for zero-overhead availability
RUN pip install --no-cache-dir semgrep detect-secrets ruff

# Application code
COPY src/ /app/src/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app

ENTRYPOINT ["/entrypoint.sh"]
