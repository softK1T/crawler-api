from app.services.adapters.shopee import ShopeeAdapter

ADAPTERS = {
    "shopee.sg": ShopeeAdapter,
    "shopee.com": ShopeeAdapter,
    "shopee.co.id": ShopeeAdapter,
    "shopee.com.my": ShopeeAdapter,
    "shopee.ph": ShopeeAdapter,
    "shopee.vn": ShopeeAdapter,
    "shopee.co.th": ShopeeAdapter,
    "shopee.com.br": ShopeeAdapter,
}


def get_adapter(url: str):
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    cls = ADAPTERS.get(domain)
    if not cls:
        raise ValueError(f"No adapter registered for domain: {domain}")
    return cls(url=url)
