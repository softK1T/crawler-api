# Crawler-API

High-performance web crawling microservice with proxy support and asynchronous task processing.

## Tech Stack

- **FastAPI** — REST API framework
- **Celery** — asynchronous task processing
- **Redis** — task broker and result caching
- **httpx** — HTTP client with HTTP/2 support
- **Docker** — containerization

## Features

- Single and batch URL crawling
- Smart proxy pool management (rotation, blocking detection, statistics)
- Asynchronous processing via Celery
- Error handling and retry mechanism
- HTTP/2 support
- Health checks and metrics

## Project Structure

```
app/
├── api/              # API endpoints
│   └── v1/
│       ├── endpoints/
│       └── router.py
├── core/             # Configuration
├── schemas/          # Pydantic models
├── services/         # Business logic
│   ├── crawler.py    # Main crawler
│   ├── batch_service.py
│   └── storage.py
└── worker/           # Celery workers
    ├── celery_app.py
    └── tasks/
```

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/softK1T/crawler-api.git
cd crawler-api
```

### 2. Create proxy file

```bash
# Format: host:port or host:port:user:pass
echo "proxy1.example.com:8080" > proxies.txt
echo "proxy2.example.com:8080:user:pass" >> proxies.txt
```

### 3. Run with Docker

```bash
docker-compose up -d
```

API available at `http://localhost:8000`

## Usage

### Single request

```bash
POST /api/v1/jobs/
{
  "url": "https://example.com",
  "headers": {"User-Agent": "MyBot/1.0"}
}
```

### Batch request

```bash
POST /api/v1/batches/
{
  "urls": ["https://example.com/1", "https://example.com/2"],
  "timeout": 30
}
```

### Check batch status

```bash
GET /api/v1/batches/{batch_id}/status
```

### Get results

```bash
GET /api/v1/batches/{batch_id}/results
```

## Configuration

Copy `.env.example` to `.env` and configure variables:

```bash
API_HOST=0.0.0.0
API_PORT=8000
PROXY_FILE=./proxies.txt
MAX_RETRIES=10
REQUEST_TIMEOUT_SECS=15
MAX_BATCH_SIZE=100
```

## API Documentation

Swagger UI available at: `http://localhost:8000/docs`

## Architecture

1. **FastAPI** receives HTTP requests
2. **Celery** processes tasks asynchronously
3. **SmartProxyPool** manages proxies (rotation, blocking detection, statistics)
4. **Redis** stores results and task state
5. **Crawler** executes HTTP requests through proxies

## Requirements

- Docker & Docker Compose
- Python 3.11+ (for local development)
- Working proxies for bypass blocking
