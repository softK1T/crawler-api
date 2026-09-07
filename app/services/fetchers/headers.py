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


def headers_for_domain(policy, *, session_key: str | None = None) -> dict[str, str]:
    """Build request headers for a domain, merging base + policy profile.

    1. Start with ``BASE_HEADERS``.
    2. Pick UA: use policy ``User-Agent`` override if present, else random.
    3. Merge any extra headers from ``policy.header_profile``.
    4. If *session_key* is given and a session exists in Redis (saved by a
       ``SiteAdapter.login()`` call — see app/services/adapters/base.py),
       inject a ``Cookie`` header so static/stealth fetchers reuse the
       authenticated session instead of hitting a login wall.
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

    # Session cookie injection (Stage 8 — login-gated adapters).
    if session_key:
        from app.services.session_manager import cookies_to_header, load_session

        cookies = load_session(session_key)
        if cookies:
            headers["Cookie"] = cookies_to_header(cookies)

    return headers
