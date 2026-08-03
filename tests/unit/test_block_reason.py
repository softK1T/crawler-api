"""Tests for normalize_block_reason and BlockReason._missing_."""

import pytest

from app.schemas.fetch import BlockReason, normalize_block_reason


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ip_ban", BlockReason.IP_BAN),
        ("captcha", BlockReason.CAPTCHA),
        ("cloudflare", BlockReason.CLOUDFLARE),
        ("rate_limited", BlockReason.RATE_LIMITED),
        ("waf", BlockReason.WAF),
        ("other", BlockReason.OTHER),
        ("bot_detection", BlockReason.OTHER),
        ("forbidden", BlockReason.IP_BAN),
        ("new_future_reason", BlockReason.OTHER),
    ],
)
def test_normalize_block_reason(value: str, expected: BlockReason) -> None:
    assert normalize_block_reason(value) is expected


def test_normalize_none() -> None:
    assert normalize_block_reason(None) is None
