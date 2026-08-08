from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RawProxy:
    url: str
    country: str | None
    proxy_type: str


class ProxyProvider(ABC):
    name: str

    @abstractmethod
    async def fetch_proxies(self) -> list[RawProxy]:
        raise NotImplementedError
