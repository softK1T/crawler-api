from __future__ import annotations

import asyncio

from app.services.proxy_providers.base import ProxyProvider, RawProxy


def _parse_line(line: str) -> RawProxy:
    host, port, user, pwd, country, proxy_type = line.split(":", maxsplit=5)
    country_value = country.upper()[:2] if country else None
    return RawProxy(
        url=f"http://{user}:{pwd}@{host}:{port}",
        country=country_value,
        proxy_type=proxy_type.lower(),
    )


class WebshareProvider(ProxyProvider):
    name = "webshare"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def fetch_proxies(self) -> list[RawProxy]:
        from app.services.webshare_sync import fetch_webshare_proxies

        lines = await asyncio.to_thread(fetch_webshare_proxies, self._api_key)
        return [_parse_line(line) for line in lines]
