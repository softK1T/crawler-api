"""Fetcher factory — returns the right FetcherProtocol implementation by engine name."""

import logging

from app.services.fetchers.base import FetcherProtocol, FetchError, FetchResult, fetch_with_retry

logger = logging.getLogger(__name__)


def get_fetcher(engine: str) -> FetcherProtocol:
    """Return a fetcher instance for *engine*.

    Supported engines: ``"httpx"``, ``"curl_cffi"``, ``"playwright"``.
    ``"camoufox"`` is mapped to ``PlaywrightFetcher`` (native support deferred).
    """
    if engine == "httpx":
        from app.services.fetchers.httpx_fetcher import HttpxFetcher

        return HttpxFetcher()
    if engine == "curl_cffi":
        from app.services.fetchers.curl_fetcher import CurlFetcher

        return CurlFetcher()
    if engine in ("playwright", "camoufox"):
        if engine == "camoufox":
            logger.warning("camoufox engine mapped to playwright; native camoufox support deferred")
        from app.services.fetchers.playwright_fetcher import PlaywrightFetcher

        return PlaywrightFetcher()
    raise ValueError(f"Unknown engine: {engine!r}")


__all__ = [
    "FetchError",
    "FetchResult",
    "FetcherProtocol",
    "fetch_with_retry",
    "get_fetcher",
]
