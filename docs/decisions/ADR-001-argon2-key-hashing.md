# ADR-001: Argon2id API Key Hashing

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 1 — Security Hardening

## Context

The crawler API uses X-API-Key header authentication. Prior to Stage 1, API keys
were stored as plaintext in the `API_KEYS_RAW` environment variable and compared
with `secrets.compare_digest`. If the environment or a log line leaked, all keys
were immediately compromised.

We need irreversible hashing so that a leaked configuration does not expose raw
keys. Verification must use constant-time comparison to prevent timing
side-channel attacks.

## Decision

**Use argon2id (via `argon2-cffi`) with the following fixed parameters:**

| Parameter | Value | Rationale |
|---|---|---|
| `time_cost` | 2 | OWASP minimum for argon2id; keeps auth latency under 5 ms per check |
| `memory_cost` | 65536 (64 MiB) | OWASP minimum; sufficient to deter GPU brute-force |
| `parallelism` | 2 | Matches a 2-vCPU container; avoids thread contention |
| `hash_len` | 32 bytes | 256-bit output — no meaningful collision risk for API keys |
| `salt_len` | 16 bytes | Standard 128-bit salt per argon2 recommendation |

### Key prefix convention

All generated API keys follow the format:

```
crw_live_<32 random url-safe bytes>
crw_test_<32 random url-safe bytes>
```

The first 8 characters (`crw_live` / `crw_test`) serve as a lookup prefix for
the in-memory key registry. At verification time, the prefix narrows the
candidate hash set before argon2 verification runs. This is a performance
optimization that will carry forward to database-backed key storage (Stage 6).

### Hash storage

For Stage 1, hashed keys are stored in memory (lazily built from
`API_KEYS_RAW` on first auth request). Raw keys in `API_KEYS_RAW` are hashed
once at startup via `hash_api_key()`. The in-memory registry is a
`dict[prefix, list[hashed_key]]` mapping.

When database-backed project storage arrives (Stage 2), the `hashed_key` will
be persisted alongside the project row.

## Alternatives Considered

### bcrypt
- **Rejected:** bcrypt has a 72-byte input limit. While API keys are shorter,
  argon2id is the current OWASP recommendation and provides better GPU
  resistance at equivalent latency.

### scrypt
- **Rejected:** Python scrypt implementations (`hashlib.scrypt`) require manual
  salt management and parameter encoding. `argon2-cffi` provides a well-tested,
  high-level API with built-in salt generation and encoded output.

### Plain SHA-256
- **Rejected:** Not a password hashing function. No salt, no work factor,
  trivially brute-forced with a GPU. Does not meet the "irreversible" requirement.

## Consequences

- **Positive:** Raw API keys never appear in memory after initial hash. A leaked
  `API_KEYS_RAW` value containing a hash does not expose the original key.
- **Positive:** Constant-time verification via argon2id's built-in comparison.
- **Negative:** Key creation requires the raw key to be hashed before storage.
  The `generate_api_key()` helper returns both raw and hashed forms for immediate
  use.
- **Negative:** `argon2-cffi` adds a compiled C extension (~2 MB wheel). Build
  environments need a C compiler (`gcc`/`clang`), already present in the
  Dockerfile.
