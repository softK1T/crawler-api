# Documentation

[Project home](../README.md)

## Choose a task

| Task | Guide |
|---|---|
| Install or update; inspect health; back up data | [Deployment and operations](runbook.md) |
| Authenticate, fetch a page, poll jobs, submit batches | [Usage guide](USER_GUIDE.md) |
| Find every HTTP method/path and its contract | [API reference](API.md) |
| Configure ports, concurrency, proxies, and optional services | [Configuration](CONFIGURATION.md) |
| Understand WARC persistence and recovery | [Local S3 and WARC](LOCAL-S3.md) |
| Import and validate proxy candidates | [Proxy candidates](PROXY_CANDIDATES.md) |

## Design and maintenance

[Architecture](ARCHITECTURE.md) · [Security boundaries](SECURITY.md) ·
[Development](DEVELOPMENT.md) · [CI policy](CI.md)

The guides above describe canonical `docker-compose.yml` on `master`. Read them in this order for a first installation: deployment, usage, API. Commands assume the repository root and Compose project `crawler-current`, unless explicitly stated otherwise.

The deployed revision's code and `/openapi.json` define the API contract. A documentation change or an added smoke test is not evidence that a deployment or CI run succeeded. Check results for the exact commit.

## Historical material

[Architecture decisions](decisions/) and [project history](PROJECT-HISTORY.md) explain past decisions, not current runtime guarantees. [Development-method notes](AI-ASSISTED-DEVELOPMENT.md) are background material, not an installation requirement.

[Legacy Ubuntu deployment](../UBUNTU.md) applies only to a separately managed external stack. Do not combine its Compose file or resource names with the canonical deployment.
