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

RUN groupadd -r crawler && useradd -r -g crawler crawler \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium for Playwright mode=browser (~400 MB).
# Install to /opt/playwright-browsers so both root (build) and crawler (runtime)
# can access.  System deps installed manually because playwright install-deps
# fails on Bookworm (renamed ttf-* font packages).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN pip install --no-cache-dir --no-deps playwright==1.49.0 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
        libcups2t64 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 \
        libasound2t64 libx11-6 libxcb1 libxext6 libxrender1 \
        fonts-liberation \
    && playwright install chromium \
    && chmod -R 755 /opt/playwright-browsers \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip /root/.cache/ms-playwright

WORKDIR /opt/crawler-api

# Copy installed packages from builder.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/
COPY pyproject.toml .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Drop privileges.
USER crawler

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
