"""Unit tests for content_decoder — all supported codecs, edge cases, and header normalization."""

import gzip
import hashlib
import zlib

import pytest

from app.services.content_decoder import (
    ContentDecodingFailed,
    UnsupportedContentEncoding,
    decode_body,
    integrity,
    normalize_headers,
)

HTML = b"<!DOCTYPE html><html><body>ok</body></html>"


# ── Roundtrip parametrized ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "enc,blob",
    [
        ("gzip", gzip.compress(HTML)),
        ("deflate", zlib.compress(HTML)),
        ("identity", HTML),
        ("", HTML),
    ],
)
def test_roundtrip(enc, blob):
    body, _orig = decode_body(blob, enc)
    assert body == HTML


def test_roundtrip_brotli():
    brotli = pytest.importorskip("brotli", reason="brotli not installed")
    body, enc = decode_body(brotli.compress(HTML), "br")
    assert body == HTML
    assert enc == "br"


def test_roundtrip_zstd():
    zstandard = pytest.importorskip("zstandard", reason="zstandard not installed")
    body, enc = decode_body(zstandard.ZstdCompressor().compress(HTML), "zstd")
    assert body == HTML
    assert enc == "zstd"


# ── Raw deflate (RFC 1951, no zlib header) ────────────────────────────────────


def test_raw_deflate():
    co = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    blob = co.compress(HTML) + co.flush()
    body, _enc = decode_body(blob, "deflate")
    assert body == HTML


# ── Gzip alias ────────────────────────────────────────────────────────────────


def test_decode_gzip_alias_x_gzip():
    raw = gzip.compress(b"hello x-gzip")
    body, _enc = decode_body(raw, "x-gzip")
    assert body == b"hello x-gzip"


# ── Identity / no encoding ────────────────────────────────────────────────────


def test_decode_identity():
    body, enc = decode_body(b"plain text", None)
    assert body == b"plain text"
    assert enc is None


def test_decode_empty_encoding():
    body, enc = decode_body(b"plain text", "")
    assert body == b"plain text"
    assert enc is None


def test_decode_identity_literal():
    body, enc = decode_body(b"identity text", "identity")
    assert body == b"identity text"
    assert enc is None


# ── Missing content-encoding → magic-byte sniffing ────────────────────────────


def test_sniff_gzip_magic():
    raw = gzip.compress(b"sniffed gzip")
    body, _enc = decode_body(raw, "")
    assert body == b"sniffed gzip"


def test_sniff_deflate_zlib_magic():
    body, _enc = decode_body(zlib.compress(b"sniffed deflate"), "")
    assert body == b"sniffed deflate"


def test_no_sniff_on_identity_bytes():
    body, enc = decode_body(b"plain uncompressed text", "")
    assert body == b"plain uncompressed text"
    assert enc is None


# ── Lying / broken content-encoding ────────────────────────────────────────────


def test_unsupported_encoding_raises():
    with pytest.raises(UnsupportedContentEncoding, match="UNSUPPORTED_CONTENT_ENCODING"):
        decode_body(HTML, "exi")


def test_broken_payload_raises():
    with pytest.raises(ContentDecodingFailed, match="CONTENT_DECODING_FAILED"):
        decode_body(b"not-gzip", "gzip")


# ── Header normalization ──────────────────────────────────────────────────────


def test_headers_normalized():
    h = normalize_headers(
        {"Content-Encoding": "br", "Content-Length": "10", "Content-Type": "text/html"},
        len(HTML),
    )
    assert "content-encoding" not in h
    assert h["content-length"] == str(len(HTML))
    assert h["content-type"] == "text/html"


def test_normalize_case_insensitive():
    headers = {
        "Content-Encoding": "gzip",
        "Content-Length": "100",
        "Content-Type": "text/plain",
    }
    result = normalize_headers(headers, 42)
    assert "content-encoding" not in result
    # Old content-length is dropped; new one is set to body_len.
    assert result["content-length"] == "42"
    assert result["content-type"] == "text/plain"


# ── Integrity fields ──────────────────────────────────────────────────────────


def test_integrity_fields():
    fields = integrity(HTML)
    assert fields["body_bytes"] == len(HTML)
    assert fields["content_sha256"] == hashlib.sha256(HTML).hexdigest()
