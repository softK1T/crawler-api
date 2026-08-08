from unittest.mock import patch

import pytest

from app.services.proxy_providers.webshare import WebshareProvider


@pytest.mark.asyncio
async def test_webshare_provider_maps_lines_to_raw_proxy() -> None:
    provider = WebshareProvider("api-key")

    with patch(
        "app.services.webshare_sync.fetch_webshare_proxies",
        return_value=["1.2.3.4:8080:user:pass:pl:residential"],
    ) as fetch_mock:
        proxies = await provider.fetch_proxies()

    fetch_mock.assert_called_once_with("api-key")
    assert len(proxies) == 1
    assert proxies[0].url.endswith("@1.2.3.4:8080")
    assert proxies[0].url.startswith("http://")
    assert proxies[0].country == "PL"
    assert proxies[0].proxy_type == "residential"
