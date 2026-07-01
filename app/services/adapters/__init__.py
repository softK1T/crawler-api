from typing import Type
from app.services.adapters.base import SiteAdapter

# Registry: domain -> SiteAdapter subclass
# Add entries here when implementing a new site adapter.
# Example:
#   from app.services.adapters.mysite import MySiteAdapter
#   ADAPTERS["mysite.com"] = MySiteAdapter
ADAPTERS: dict[str, Type[SiteAdapter]] = {}


def get_adapter(url: str) -> SiteAdapter:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    cls = ADAPTERS.get(domain)
    if not cls:
        raise ValueError(
            f"No adapter registered for domain: {domain!r}. "
            f"Register one in app/services/adapters/__init__.py"
        )
    return cls(url=url)
