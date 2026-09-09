from __future__ import annotations

import asyncio

from app.services.proxy_providers.base import ProxyProvider, RawProxy


class WebshareProvider(ProxyProvider):
    name = "webshare"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def fetch_proxies(self) -> list[RawProxy]:
        from app.services.webshare_sync import fetch_all_webshare_proxies

        return await asyncio.to_thread(fetch_all_webshare_proxies, self._api_key)
