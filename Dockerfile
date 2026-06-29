FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# All system deps required by Camoufox (Firefox-based) and Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # XPCOM / Firefox core — the ones camoufox actually needs
    libasound2 \
    libgtk-3-0 \
    libglib2.0-0 \
    libdbus-glib-1-2 \
    libdbus-1-3 \
    # X11
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcb-shm0 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libxkbcommon0 \
    # GL / DRM
    libdrm2 \
    libgbm1 \
    # Network / Security
    libnss3 \
    libnspr4 \
    # ATK / Accessibility
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    # Pango / Cairo fonts
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    # Fonts
    fonts-liberation \
    fontconfig \
    # Misc
    ca-certificates \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch Camoufox Firefox build at image build time (avoids download at runtime)
RUN python -m camoufox fetch

COPY app app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
