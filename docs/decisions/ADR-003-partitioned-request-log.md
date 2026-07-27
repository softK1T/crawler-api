# ADR-003: Partitioned Request Log Table

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 2 — Data Model + Alembic

## Context

The `request_log` table records every outbound HTTP request the platform makes on
behalf of clients. In a steady-state deployment processing ~1M requests/day, this
table grows by ~30M rows/month. Without partitioning:

1. Index scans on `(application_id, requested_at DESC)` degrade to sequential
   scans once the table exceeds available memory.
2. Purging old data (e.g., `DELETE WHERE requested_at < '2025-01-01'`) locks
   the table for minutes and generates massive WAL traffic.
3. Vacuum maintenance cannot keep up with the insert + delete churn.

## Decision

**Range-partition `request_log` on `requested_at` by year.**

| Aspect | Choice | Rationale |
|---|---|---|
| Partition key | `requested_at` (TIMESTAMPTZ) | Most queries include a time filter; partition pruning eliminates irrelevant partitions |
| Granularity | Annual (yyyy-01-01) | Balances partition count (~10 over a decade) with scan efficiency |
| Composite PK | `(id, requested_at)` | Required by PostgreSQL for any partitioned table that has a unique/primary constraint |
| Initial partitions | 2026, 2027 | Created manually in the migration via `op.execute()` |
| Future partitions | Manual `CREATE TABLE … PARTITION OF` | Alembic cannot auto-generate partition DDL; operations must create new year partitions annually |

### Soft link from `warc_index`

`warc_index.request_log_id` is a plain UUID column, **not** a foreign key.
PostgreSQL does not allow foreign keys referencing partitioned tables (the
referenced row could be in any partition, and FK enforcement would require a
full scan across all partitions). Application code validates the referential
integrity at insert time.

### Sub-partitioning

No sub-partitioning (e.g., by `application_id`) is applied. The primary query
patterns are time-range scans; the `(application_id, requested_at DESC)` and
`(domain, requested_at DESC)` indexes handle per-application and per-domain
lookups efficiently within a year partition.

## Alternatives Considered

### Monthly partitioning
- **Rejected:** Produces 120+ partitions over a decade. PostgreSQL handles this
  but partition metadata overhead grows; pruning 120 partitions during planning
  adds ~1-2ms per query.

### No partitioning, rely on BRIN indexes
- **Rejected:** BRIN indexes on `requested_at` work well for append-only
  time-series but don't help with `application_id`-filtered lookups.

### TimescaleDB hypertable
- **Rejected:** Adds an extension dependency and operational complexity.
  PostgreSQL native partitioning in v14+ is sufficient for this scale.

## Consequences

- **Positive:** `DELETE` on old partitions becomes `DROP TABLE`, which is O(1)
  and generates no WAL bloat.
- **Positive:** Partition pruning eliminates irrelevant years from sequential
  scans, keeping full-table scans bounded.
- **Negative:** New year partitions must be created manually before the new year
  or at the first insert for that year (which will fail with "no partition of
  relation").
- **Negative:** FK from `warc_index` to `request_log` is impossible.
  Application-level validation is required and will be enforced at the WARC
  writer service layer (Stage 5).
- **Operational note:** Add a yearly cron/beat task that creates the next year's
  partition in November (well before the boundary). The task creates the
  partition with `IF NOT EXISTS` for idempotency.
