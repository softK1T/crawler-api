"""Content-Encoding decoder with magic-byte sniffing.

Normalizes compressed HTTP response bodies for API consumers while
preserving raw transport bytes for WARC archival.

Supported encodings: gzip, deflate (zlib + raw), brotli, zstandard.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import zlib

import brotli
import zstandard

from app.core.url_guard import MAX_BODY_BYTES, BodyTooLarge

_SUPPORTED = {"br", "gzip", "x-gzip", "deflate", "zstd", "identity", ""}


class UnsupportedContentEncoding(ValueError):
    """Raised when the Content-Encoding value is not in the supported set."""


class ContentDecodingFailed(ValueError):
    """Raised when decoding fails for a declared encoding (broken payload)."""


def sniff(raw: bytes) -> str | None:
    """Guess content encoding from magic bytes."""
    if raw[:2] == b"\x1f\x8b":
        return "gzip"
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    # zlib header check (RFC 1950): CMF byte & 0x0F == 8, (CMF<<8|FLG) % 31 == 0.
    if len(raw) >= 2 and raw[0] & 0x0F == 8 and (raw[0] << 8 | raw[1]) % 31 == 0:
        return "deflate"
    return None


def _looks_like_text(b: bytes) -> bool:
    """Heuristic: does *b* look like plain text (HTML, JSON, XML)?"""
    head = b[:512].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")) or (
        b"\x00" not in b[:512]
    )


def _decode_brotli(raw: bytes) -> bytes:
    decoder = brotli.Decompressor()
    out = bytearray()
    data = raw
    while True:
        chunk = decoder.process(data, output_buffer_limit=MAX_BODY_BYTES + 1 - len(out))
        out.extend(chunk)
        if len(out) > MAX_BODY_BYTES:
            raise BodyTooLarge("Decoded response exceeds body limit")
        if decoder.is_finished():
            return bytes(out)
        if decoder.can_accept_more_data():
            raise brotli.error("Incomplete brotli response")
        data = b""


def _decode_deflate(raw: bytes, wbits: int) -> bytes:
    decoder = zlib.decompressobj(wbits)
    body = decoder.decompress(raw, MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES or decoder.unconsumed_tail:
        raise BodyTooLarge("Decoded response exceeds body limit")
    if not decoder.eof:
        raise zlib.error("Incomplete deflate response")
    return body


def decode_body(raw: bytes, encoding: str | None) -> tuple[bytes, str | None]:
    """Decode *raw* bytes according to *encoding*.

    Returns ``(decoded_bytes, detected_encoding)``.
    """
    if len(raw) > MAX_BODY_BYTES:
        raise BodyTooLarge("Response exceeds body limit")
    enc = (encoding or "").strip().lower().split(",")[0].strip()

    if enc not in _SUPPORTED:
        raise UnsupportedContentEncoding(f"UNSUPPORTED_CONTENT_ENCODING: {enc}")

    if enc in ("", "identity"):
        sniffed = sniff(raw)
        if sniffed is None:
            # Brotli has no reliable magic — try it as fallback for non-text bytes.
            if not _looks_like_text(raw):
                try:
                    return _decode_brotli(raw), "br"
                except brotli.error:
                    pass
            return raw, None
        enc = sniffed

    try:
        if enc == "br":
            return _decode_brotli(raw), "br"
        if enc in ("gzip", "x-gzip"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                body = stream.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise BodyTooLarge("Decoded response exceeds body limit")
            return body, enc
        if enc == "deflate":
            try:
                return _decode_deflate(raw, zlib.MAX_WBITS), "deflate"
            except zlib.error:
                return _decode_deflate(raw, -zlib.MAX_WBITS), "deflate"
        if enc == "zstd":
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as stream:
                body = stream.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise BodyTooLarge("Decoded response exceeds body limit")
            return body, "zstd"
    except BodyTooLarge:
        raise
    except Exception as exc:
        raise ContentDecodingFailed(f"CONTENT_DECODING_FAILED: encoding={enc}: {exc}") from exc

    return raw, None


def normalize_headers(headers: dict[str, str], body_len: int) -> dict[str, str]:
    """Return headers safe for API consumers.

    Drops ``content-encoding`` and old ``content-length`` (refers to
    compressed representation).  Sets ``content-length`` to *body_len*.
    """
    out = {
        k.lower(): v
        for k, v in headers.items()
        if k.lower() not in ("content-encoding", "content-length")
    }
    out["content-length"] = str(body_len)
    return out


def integrity(body: bytes) -> dict:
    """Return sha256 hash and byte count for *body*."""
    return {
        "body_bytes": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }


__all__ = [
    "ContentDecodingFailed",
    "UnsupportedContentEncoding",
    "decode_body",
    "integrity",
    "normalize_headers",
    "sniff",
]
