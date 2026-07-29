# ADR-010: Archive Read API

**Status:** Accepted | **Date:** 2026-07-27 | **Stage:** 9

## Context
WARC files are uploaded to S3 and indexed in warc_index (Stage 7). Callers need to retrieve archived content by request_id or search by URL/date range. The naive approach — downloading the entire WARC file and scanning for a record — is bandwidth-prohibitive for multi-GB files.

## Decision

### S3 range-read via warc_index byte offsets
The warc_index table stores (warc_filename, offset, length) for each record. ArchiveReader issues an S3 GetObject with a Range header:

```
Range: bytes={offset}-{offset + length - 1}
```

This downloads only the requested record (~10-100KB) rather than the entire WARC file (~200MB-1GB). warcio.ArchiveIterator parses the gzip-compressed byte range and extracts the HTTP response payload.

### Revisit record resolution
Revisit records (is_revisit=True) contain no body — they reference the original by sha256. When a caller requests a revisit entry, the API resolves the chain:

```
SELECT warc_index WHERE sha256 = :sha256 AND is_revisit = FALSE
ORDER BY captured_at ASC LIMIT 1
```

The original (non-revisit) record's (warc_filename, offset, length) is used for the S3 range-read. The response metadata still reflects the revisit entry (is_revisit=True).

### List vs content endpoint split
`GET /v1/archive` returns metadata only (ArchiveEntryResponse, no body bytes). `GET /v1/archive/{request_id}` returns full content with base64-encoded body. This keeps list responses small regardless of archived content size and prevents accidental large downloads from list queries.

### Deferred: chunked streaming
Base64-encoding large bodies in JSON is suboptimal for >10MB responses. A chunked streaming endpoint (Transfer-Encoding: chunked with raw WARC bytes) is deferred post-Stage 13. For now, all responses fit in the JSON envelope.
