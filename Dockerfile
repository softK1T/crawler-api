FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Exact deps required by Camoufox (Firefox-based) on Debian Bookworm/Slim
# Source: https://camoufox.com/python/installation/ + GitHub issue #44 + issue #311
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core Firefox/XPCOM deps (official minimum)
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    # Extra deps from real-world Docker deployments (issue #311)
    libgbm1 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxkbcommon0 \
    fonts-liberation \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch Camoufox Firefox build at image build time (avoids runtime download)
RUN python -m camoufox fetch

COPY app app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
