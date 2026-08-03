# Stage 1: builder — compile Python dependencies.
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install core + browser (worker needs Playwright/Camoufox; API shares the image).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[browser]"

# Stage 2: runtime — minimal production image.
FROM python:3.12-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd -r crawler && useradd -r -g crawler crawler \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/crawler-api

# Copy installed packages from builder.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Install Chromium runtime deps manually — avoids ttf-unifont/ttf-ubuntu-font-family
# missing on Debian Trixie arm64 (Mac M-series). Works on both amd64 and arm64.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && python -m playwright install --only-shell chromium \
    && chmod -R a+rx /ms-playwright

# Copy application code.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/
COPY pyproject.toml .

# Drop privileges.
USER crawler

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
