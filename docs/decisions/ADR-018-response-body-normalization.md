# ADR-018: Response Body Normalization

**Date:** 2026-08-03
**Status:** Accepted

## Context

HTTP responses may arrive with `Content-Encoding: gzip`, `br`, `zstd`, or
`deflate`. Currently the fetchers store whatever bytes the HTTP library
returns:

- **httpx** returns `response.content` — already decompressed by httpx
  internally, with original `content-encoding` header still present.
- **curl_cffi** returns `resp.content` — curl auto-decodes, same issue.
- **playwright** renders the DOM to UTF-8 — headers and transport bytes
  are lost entirely.

This creates two problems:

1. **API consumers** receive a `body_b64` that may or may not be
   compressed, with `content-encoding` headers that don't match the
   actual bytes. Consumers must implement their own decompression
   logic — duplicating complexity and risking silent-garbage failures.
2. **WARC records** should preserve the exact bytes received from the
   server, including original compression. The current approach stores
   already-decoded bytes, losing fidelity.

## Decision

### Server-side decompression for API, raw bytes for WARC

The worker normalizes response bodies before storing the API result:

```
Transport bytes (raw) → WARC
                      → decode → normalized headers → API result
```

### Fetcher changes

**httpx fetcher** switches from `client.get()` to `client.stream()` +
`response.aiter_raw()` to capture raw transport bytes before httpx
applies content decoding. The body is then separately decoded via the
content-decoder module.

**curl_cffi fetcher** sets `raw_body = body` (curl auto-decodes, no
raw streaming interface available in curl_cffi).

**playwright fetcher** sets `raw_body = body` (the "body" is rendered
DOM HTML — there are no transport bytes in browser mode).

### Content decoder (`app/services/content_decoder.py`)

Supports: gzip, deflate (zlib-wrapped + raw RFC 1951), brotli, zstd.

Magic-byte sniffing for responses with missing or incorrect
`content-encoding` header:

| Magic bytes | Encoding |
|---|---|
| `1f 8b` | gzip |
| `28 b5 2f fd` | zstd |
| Valid zlib header | deflate |

`UnsupportedContentEncoding` is raised when the declared encoding is
not supported AND `strict=True`. When decoding fails on a declared
encoding, the job fails with `CONTENT_DECODING_FAILED` — no garbage
is returned to the caller.

### API result schema v2

New additive fields in `FetchResultSchema`:

```json
{
  "api_version": "2",
  "body_b64": "<decompressed bytes, base64>",
  "body_is_compressed": false,
  "body_bytes": 12345,
  "content_sha256": "hex...",
  "original_content_encoding": "br",
  "headers": {
    "content-type": "text/html"
  }
}
```

`content-encoding` and `content-length` are stripped from
`headers` since they refer to the compressed representation.

All existing fields (`body_b64`, `headers`, etc.) remain —
this is purely additive.

### WARC storage

`WarcStorage.archive()` accepts an optional `warc_body` parameter.
When provided, it is used as the WARC payload instead of
`fetch_result.body`. This ensures the WARC record contains the
original transport bytes.

## Alternatives considered

1. **Document that clients must decompress.** Rejected: pushes
   complexity to every consumer, risks silent garbage when a consumer
   forgets to check `content-encoding`.

2. **Store both compressed and decompressed in the API result.**
   Rejected: doubles payload size with no benefit — the raw bytes
   are already in WARC.

3. **Use `response.read()` instead of `aiter_raw()`.** Rejected:
   `read()` returns decoded bytes in httpx. Only `aiter_raw()`
   provides the raw transport stream.

## Consequences

- `brotli` and `zstandard` added to base dependencies.
- httpx fetcher uses streaming API — slightly higher overhead per
  request but no change to the connection pool (each request already
  creates a fresh client; ADR-007).
- API consumers can unconditionally treat `body_b64` as decompressed.
- WARC records preserve original Content-Encoding for archival
  fidelity.
