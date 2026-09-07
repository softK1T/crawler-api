from app.services.adapters.base import SiteAdapter
from app.services.adapters.example_site import ExampleSiteAdapter
from app.services.adapters.quotes_toscrape import QuotesToScrapeAdapter

# Registry: domain -> SiteAdapter subclass
# Add entries here when implementing a new site adapter.
ADAPTERS: dict[str, type[SiteAdapter]] = {
    "example.com": ExampleSiteAdapter,
    "quotes.toscrape.com": QuotesToScrapeAdapter,
}


def get_adapter(url: str) -> SiteAdapter:
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.removeprefix("www.")
    cls = ADAPTERS.get(domain)
    if not cls:
        raise ValueError(
            f"No adapter registered for domain: {domain!r}. "
            f"Register one in app/services/adapters/__init__.py"
        )
    return cls(url=url)
