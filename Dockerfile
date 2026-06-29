FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# System deps required by Firefox/Camoufox and Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # GTK3 + GLib (Firefox/Camoufox)
    libgtk-3-0 \
    libglib2.0-0 \
    libdbus-glib-1-2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
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
