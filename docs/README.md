# Documentation

[Project home](../README.md)

| Goal | Guide |
|---|---|
| First client key, page and batch | [User guide](USER_GUIDE.md) |
| Routes, scopes, schemas and errors | [API reference](API.md) |
| Components and request lifecycle | [Architecture](ARCHITECTURE.md) |
| Environment, defaults and integrations | [Configuration](CONFIGURATION.md) |
| Real proxy and ten-page acceptance | [Proxy candidates](PROXY_CANDIDATES.md) |
| Backups, recovery and diagnostics | [Runbook](runbook.md) |
| Local changes and tests | [Development](DEVELOPMENT.md) |
| Fast/full validation and resource cost | [CI policy](CI.md) |
| Exposure and known boundaries | [Security](SECURITY.md) |
| Existing external server | [Ubuntu deployment](../UBUNTU.md) |

Reading paths: users → user guide/API; developers → architecture/development;
operators → configuration/runbook/security.

## Evidence

Reconciled against interview-stable at a84263b on 2026-11-19. Inspected GitHub checks
reported fast, integration, Docker/browser/Compose and CI gate success. Docker
includes the basic disposable restore drill from c1305e6. This does not certify
real OLX/proxy success, sustained load, optional integrations or production security.
The external server was separately observed at a21a48e / DB 0009 / API port 32770;
repository commits do not deploy there. PR #19 is not yet merged.

## Current versus historical

Code and live OpenAPI for the deployed revision define the contract; current guides
explain canonical deployment. Historical reports/ADRs explain earlier decisions.
Do not treat their old commands, counts or deferred-feature lists as current state.

Historical: [project history](PROJECT-HISTORY.md), [ADRs](decisions/),
[development method](AI-ASSISTED-DEVELOPMENT.md), [original audit](audit.md),
[August audit](STATUS-AUDIT-2026-08-30.md).
