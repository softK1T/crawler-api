# ADR-012: Infrastructure and Deployment

**Status:** Accepted | **Date:** 2026-07-27 | **Stage:** 12

## Context
The platform needs a deployment target. Constraints: budget EUR 20/month, single maintainer, no dedicated ops team.

## Decision

### Hetzner Cloud, single server
- **cx31** (8 GB RAM, 2 vCPU, EUR ~12/month with volume)
- Matches the target hardware profile (i5-8600 equivalent, 16 GB RAM)
- Ubuntu 22.04 LTS

### Docker Compose (no Kubernetes)
- Single-node deployment. Docker Compose is simpler to maintain and debug.
- Kubernetes deferred until multi-node scaling is needed (Stage 14+).

### Terraform for infrastructure-as-code
- Hetzner Cloud provider (`hetznercloud/hcloud ~> 1.45`)
- Server + attached volume + firewall
- Cloud-init user-data provisions Docker and clones repo

### CI/CD via GitHub Actions
- Self-hosted runners not required — public runners are sufficient.
- Docker build-only (no push) in CI; deployment is manual or via Terraform.

## Consequences
- Single point of failure (one server). Acceptable for the current non-critical workload.
- No auto-scaling. Manual server resize via Terraform if needed.
- Volume-attached Postgres data survives server recreation.
