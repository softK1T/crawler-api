"""Content-Encoding decoder with magic-byte sniffing.

Normalizes compressed HTTP response bodies for API consumers while
preserving raw transport bytes for WARC archival.

Supported encodings: gzip, deflate (zlib + raw), brotli, zstandard.
"""

import gzip
import logging
import zlib

logger = logging.getLogger(__name__)

# ── Magic bytes ────────────────────────────────────────────────────────────────

_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


class UnsupportedContentEncoding(ValueError):
    """Raised when the Content-Encoding value is not supported or decoding fails."""


def _sniff_encoding(raw: bytes) -> str | None:
    """Guess the content encoding from magic bytes in *raw*.

    Returns the encoding name or ``None`` if identity.
    """
    if raw[:2] == _GZIP_MAGIC:
        return "gzip"
    if raw[:4] == _ZSTD_MAGIC:
        return "zstd"
    # Try zlib-wrapped deflate (RFC 1950).
    try:
        zlib.decompress(raw)
        return "deflate"
    except zlib.error:
        pass
    # Try raw deflate (RFC 1951, no header).
    try:
        zlib.decompress(raw, -zlib.MAX_WBITS)
        return "deflate"
    except zlib.error:
        pass
    return None


def decode_body(
    raw: bytes,
    encoding: str | None,
    *,
    strict: bool = True,
) -> tuple[bytes, str | None]:
    """Decode *raw* bytes according to *encoding*.

    Args:
        raw: Raw transport bytes (as received from the wire).
        encoding: ``Content-Encoding`` header value (case-insensitive).
        strict: If ``True``, raise on unsupported encoding.  If ``False``,
            return *raw* unchanged (identity fallback).

    Returns:
        ``(decoded_bytes, detected_encoding)`` where *detected_encoding* is
        the encoding that was actually applied (``None`` for identity).
    """
    normalized = (encoding or "").strip().lower()

    # Map common aliases.
    if normalized in ("", "identity", "none"):
        normalized = ""

    if not normalized:
        sniffed = _sniff_encoding(raw)
        if sniffed is not None:
            logger.debug("Sniffed encoding=%s for %d bytes", sniffed, len(raw))
            return decode_body(raw, sniffed, strict=strict)
        return raw, None

    try:
        if normalized == "br":
            import brotli

            return brotli.decompress(raw), "br"
        if normalized in ("gzip", "x-gzip"):
            return gzip.decompress(raw), normalized
        if normalized == "deflate":
            try:
                return zlib.decompress(raw), "deflate"
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS), "deflate"
        if normalized in ("zstd", "zstandard"):
            import zstandard

            return zstandard.ZstdDecompressor().decompress(raw), normalized
    except Exception as exc:
        if strict:
            raise UnsupportedContentEncoding(
                f"CONTENT_DECODING_FAILED: {normalized} — {exc}"
            ) from exc
        logger.warning("Content decoding failed for %s: %s", normalized, exc)
        return raw, normalized

    if strict:
        raise UnsupportedContentEncoding(f"UNSUPPORTED_CONTENT_ENCODING: {normalized!r}")
    logger.warning("Unsupported content encoding: %r — returning identity", normalized)
    return raw, normalized


def normalize_response_headers(
    headers: dict[str, str],
    original_encoding: str | None,
) -> dict[str, str]:
    """Return headers safe for API consumers.

    Removes ``content-encoding`` (already decompressed) and
    ``content-length`` (refers to compressed representation).
    All other headers pass through unchanged.
    """
    drop = {"content-encoding", "content-length"}
    return {k: v for k, v in headers.items() if k.lower() not in drop}


def compute_integrity_fields(decoded_body: bytes, original_encoding: str | None) -> dict:
    """Return api_version=2 integrity fields for a decoded body."""
    import base64
    import hashlib

    return {
        "api_version": "2",
        "body_b64": base64.b64encode(decoded_body).decode("ascii"),
        "body_is_compressed": False,
        "body_bytes": len(decoded_body),
        "content_sha256": hashlib.sha256(decoded_body).hexdigest(),
        "original_content_encoding": original_encoding,
    }


__all__ = [
    "UnsupportedContentEncoding",
    "compute_integrity_fields",
    "decode_body",
    "normalize_response_headers",
]
