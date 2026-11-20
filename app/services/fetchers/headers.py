"""User-Agent profiles and header builder for fetchers."""

import random

UA_PROFILES: dict[str, str] = {
    "chrome_win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "chrome_mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "firefox_win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"
    ),
    "chrome_mobile": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
}

BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def random_ua() -> str:
    """Return a random User-Agent string from UA_PROFILES."""
    return random.choice(list(UA_PROFILES.values()))


def headers_for_domain(policy) -> dict[str, str]:
    """Build request headers for a domain, merging base + policy profile.

    1. Start with ``BASE_HEADERS``.
    2. Pick UA: use policy ``User-Agent`` override if present, else random.
    3. Merge any extra headers from ``policy.header_profile``.
    """
    headers = dict(BASE_HEADERS)

    # User-Agent
    if policy is not None and policy.header_profile and "User-Agent" in policy.header_profile:
        headers["User-Agent"] = policy.header_profile["User-Agent"]
    else:
        headers["User-Agent"] = random_ua()

    # Merge policy header profile overrides.
    if policy is not None and policy.header_profile:
        headers.update(policy.header_profile)

    return headers


# Engines that present a browser fingerprint build their own header set. Generic
# identity headers on top of it contradict the TLS and JavaScript fingerprint.
FINGERPRINTED_ENGINES = frozenset({"curl_cffi", "playwright", "camoufox"})
IDENTITY_HEADERS = frozenset(
    {
        "user-agent",
        "accept",
        "accept-encoding",
        "accept-language",
        "connection",
        "upgrade-insecure-requests",
    }
)

# Language a browser in the proxy's country would normally announce.
ACCEPT_LANGUAGE_BY_COUNTRY: dict[str, str] = {
    "PL": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "FR": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "GB": "en-GB,en;q=0.9",
    "US": "en-US,en;q=0.9",
}


def headers_for_engine(
    policy: object | None, engine: str, proxy_country: str | None = None
) -> dict[str, str]:
    """Headers to add on top of what *engine* sends by itself.

    httpx has no browser fingerprint and keeps the generic set. curl_cffi and the
    browsers get only non-identity headers from the policy; curl_cffi also gets an
    Accept-Language matching the proxy country unless the policy sets one.
    """
    if engine not in FINGERPRINTED_ENGINES:
        return headers_for_domain(policy)
    profile: dict[str, str] = getattr(policy, "header_profile", None) or {}
    headers = {
        name: value for name, value in profile.items() if name.lower() not in IDENTITY_HEADERS
    }
    if engine == "curl_cffi":
        language = next(
            (value for name, value in profile.items() if name.lower() == "accept-language"),
            ACCEPT_LANGUAGE_BY_COUNTRY.get((proxy_country or "").strip().upper()),
        )
        if language:
            headers["Accept-Language"] = language
    return headers
