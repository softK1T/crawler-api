from unittest.mock import patch

import pytest

from app.services.proxy_providers.base import RawProxy
from app.services.proxy_providers.webshare import WebshareProvider


@pytest.mark.asyncio
async def test_webshare_provider_returns_raw_proxies_from_aggregator() -> None:
    provider = WebshareProvider("api-key")
    expected = [
        RawProxy(
            url="http://user:pass@1.2.3.4:8080",
            country="PL",
            city="Warsaw",
            proxy_type="residential",
        )
    ]

    with patch(
        "app.services.webshare_sync.fetch_all_webshare_proxies",
        return_value=expected,
    ) as fetch_mock:
        proxies = await provider.fetch_proxies()

    fetch_mock.assert_called_once_with("api-key")
    assert proxies == expected
    assert proxies[0].country == "PL"
    assert proxies[0].city == "Warsaw"
    assert proxies[0].proxy_type == "residential"
