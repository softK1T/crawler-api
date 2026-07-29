# ADR-008: WARC Storage Design

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 7 — WARC Storage

## Context

The platform archives every crawled page to WARC (Web ARChive) format for
long-term storage, replay, and compliance. WARC files must be uploaded to S3
(or MinIO), indexed in the `warc_index` table with byte-offset metadata for
random-access retrieval, and deduplicated by content hash.

## Decision

### In-memory WARC buffer

`WarcWriter` accumulates WARC records in an in-memory `io.BytesIO` buffer
(not a temp file). At 1 GB max size, this requires ~1 GB RAM. The target
server has 16 GB RAM — acceptable. A temp-file buffer is deferred as a
future optimization for environments with lower memory.

### Single writer, no locks

`WarcWriter` is NOT thread-safe. It lives as a single instance on
`app.state.warc_storage` and is accessed only from the async event-loop
thread. The deployment model (single API process with uvicorn) guarantees
that only one thread accesses the writer at a time.

### Dedup: (url, sha256) pair

```sql
SELECT warc_index WHERE sha256 = :sha256 AND url = :url
ORDER BY captured_at DESC LIMIT 1
```

Matching on BOTH url AND sha256 means:
- Same URL, different content → new response record (content changed legitimately)
- Different URL, same sha256 → new response record (different resource, coincidental hash match — extremely rare with SHA-256)
- Same URL, same sha256 → revisit record (genuine duplicate — same page recrawled without changes)

### Rotation by size or time, whichever comes first

`needs_rotation()` returns True when the buffer exceeds 1 GB OR the writer is
older than 1 hour. This caps data loss on crash to at most 1 hour of crawling.

### S3 upload failure → discard

If S3 upload fails (network error, auth error, bucket missing), the WARC
buffer is discarded and a new writer is started. Data loss is accepted for
two reasons:
1. The primary storage is the `crawl_results` table (WARC is archival, not
   the source of truth).
2. Retrying S3 uploads with exponential backoff would block the WARC buffer
   and stall all crawling. Crawl throughput > archival completeness.

This is documented as a known limitation. A future stage could add a
dead-letter queue for failed WARC uploads.

### S3 not configured → skip upload

If `S3_ACCESS_KEY` is empty, `_rotate()` logs a warning and discards the
buffer without upload. This allows the application to run without S3 in
development and testing — WARC data is simply not persisted.

## Alternatives Considered

### Write WARC to a temp file on disk, then stream to S3
- **Rejected for now:** Adds disk I/O complexity and requires temp directory
  management. In-memory buffer is simpler and sufficient for the current scale.

### Dedup only by sha256 (not url)
- **Rejected:** Two different URLs with the same content (e.g., two mirrors
  of the same page) should each produce a distinct response record. Revisit
  records represent the SAME resource being recrawled, not different resources
  with identical content.

### Synchronous boto3 (not aioboto3)
- **Rejected:** The S3 upload happens on the critical path (during a crawl
  request), so it must be non-blocking. aioboto3 wraps botocore with asyncio.

## Consequences

- **Positive:** WARC files are gzip-compressed on the fly — 1 GB of raw
  HTML typically compresses to 200-400 MB on S3.
- **Positive:** Byte-offset indexing in `warc_index` enables O(1) random
  access to a specific record within a WARC file.
- **Negative:** In-memory buffer means ~1 GB RAM dedicated to WARC. At high
  throughput, rotation may occur every few seconds, triggering frequent S3
  uploads.
- **Negative:** S3 upload failure discards data. A dead-letter queue or
  local backup is recommended for production deployments where archival
  completeness matters.

## Stage 15 update (ADR-015)

Superseded by ADR-015 (Stage 15): rotation flag, dead-letter queue,
streaming archive endpoint, and DLQ retry cron.
