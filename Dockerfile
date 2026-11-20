# Stage 1: builder — compile Python dependencies.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install core + browser (worker needs Playwright/Camoufox; API shares the image).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[browser]"

# Stage 2: runtime — minimal production image.
FROM python:3.12-slim-bookworm

# The recovered browser artifact is Linux x86_64 only. Do not install it on ARM64.
RUN test "$(dpkg --print-architecture)" = amd64 \
    || (echo "Pinned Camoufox runtime currently requires Linux amd64" >&2; exit 1)

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/crawler \
    XDG_CACHE_HOME=/home/crawler/.cache \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAX_CONCURRENT_CAMOUFOX=1

RUN groupadd -r crawler \
    && useradd -r -g crawler -m -d /home/crawler crawler \
    && mkdir -p /home/crawler/.cache/python-tldextract \
    && chown -R crawler:crawler /home/crawler \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/crawler-api

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Runtime libraries for Chromium and Camoufox; Xvfb supports virtual display mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
    libgtk-3-0 libdbus-glib-1-2 libxt6 libx11-xcb1 libxcb-shm0 \
    xvfb \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && python -m playwright install chromium \
    && chmod -R a+rx /ms-playwright

# Pinned recovered installation: do not query the unavailable upstream releases API.
# Verify the compressed bytes before extracting. A changed release asset fails closed.
RUN curl --fail --location --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 1800 \
      --output /tmp/camoufox.tar.gz \
      https://github.com/softK1T/crawler-api/releases/download/runtime-camoufox-159.0-beta.42-linux-x86_64/camoufox-linux-x86_64.tar.gz \
    && printf '%s  %s\n' '549445fd1a070901cfb7e3f1309e0613f6dd117180d1cd01edd2df61606f9804' '/tmp/camoufox.tar.gz' | sha256sum --check --strict - \
    && python -c 'import json, os, pathlib, sys, tarfile; archive = tarfile.open("/tmp/camoufox.tar.gz", "r:gz"); archive.extractall("/home/crawler/.cache", filter="data"); archive.close(); cache = pathlib.Path("/home/crawler/.cache/camoufox"); metadata = json.loads((cache / "version.json").read_text()); expected = {"version": "159.0", "release": "beta.42"}; metadata == expected or sys.exit("Unexpected Camoufox version"); any(p.is_file() and os.access(p, os.X_OK) for p in (cache / "camoufox", cache / "camoufox-bin")) or sys.exit("Camoufox executable missing or not executable"); print("Pinned Camoufox 159.0-beta.42 verified")' \
    && chown -R crawler:crawler /home/crawler/.cache/camoufox \
    && rm /tmp/camoufox.tar.gz

# GeoIP data for Camoufox geoip=True (aligns timezone and locale with the proxy exit country).
# Camoufox downloads it lazily into its package directory, which the runtime user cannot write.
# A failed download is not fatal: the fetcher then runs Camoufox without geoip.
RUN GEOIP=/usr/local/lib/python3.12/site-packages/camoufox/GeoLite2-City.mmdb; \
    ( curl --fail --location --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 600 \
        --output "$GEOIP" \
        https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-City.mmdb \
      && python -c 'import sys, maxminddb; db = maxminddb.open_database(sys.argv[1]); kind = db.metadata().database_type; db.close(); "City" in kind or sys.exit("Unexpected GeoIP database: " + kind); print("GeoIP database installed:", kind)' "$GEOIP" \
      && chmod a+r "$GEOIP" ) \
    || { rm -f "$GEOIP"; echo "WARNING: GeoIP database not installed; Camoufox will run without geoip" >&2; }

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/
COPY pyproject.toml .

USER crawler

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
