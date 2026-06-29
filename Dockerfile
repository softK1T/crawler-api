FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

WORKDIR /app

# System deps for Camoufox (Firefox) + Xvfb virtual display
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core Firefox/XPCOM deps
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    # Extra deps for headless stability
    libgbm1 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxkbcommon0 \
    # Xvfb virtual display (recommended by camoufox for headless)
    xvfb \
    fonts-liberation \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch Camoufox Firefox build at image build time
RUN python -m camoufox fetch

COPY app app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
