"""Login adapter for quotes.toscrape.com — simple form + CSRF token, no JS.

Reference target: no anti-bot, no residential proxy needed. This adapter uses
plain httpx (no browser) because the login form is static HTML with a hidden
CSRF token — exactly the case described in the runbook as "simple site,
cookie-gated, no JS challenge". DomainPolicy for this domain should stay at
tier 0 (engine=httpx, use_proxy=False); no residential/datacenter proxy pool
is required for either login or subsequent fetches.
"""

import re

import httpx

from app.services.adapters.base import SiteAdapter

_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


class QuotesToScrapeAdapter(SiteAdapter):
    """Login adapter for the public quotes.toscrape.com demo site.

    No residential proxy pool is required: DomainPolicy for this domain can
    use ``use_proxy=False`` and ``engine="httpx"`` (escalation tier 0). This
    adapter exists purely to obtain a session cookie; DO NOT wire a proxy pool
    to it — the target explicitly has no anti-bot layer.
    """

    session_key = "quotes.toscrape.com"
    login_url = "https://quotes.toscrape.com/login"

    async def login(
        self,
        username: str,
        password: str,
        proxy_url: str | None = None,
    ) -> dict[str, str] | None:
        """Fetch the login page for a CSRF token, then POST credentials.

        *proxy_url* is accepted for interface compatibility with
        ``SiteAdapter.login()`` but intentionally unused here: this target
        does not require a proxy of any kind (no rate-limit/anti-bot wall).
        """
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            # 1. GET the login page to obtain the CSRF token + initial cookies.
            get_resp = await client.get(self.login_url)
            match = _CSRF_RE.search(get_resp.text)
            if not match:
                return None
            csrf_token = match.group(1)

            # 2. POST credentials + CSRF token. httpx.AsyncClient keeps the
            #    session cookie jar across requests within the same client.
            post_resp = await client.post(
                self.login_url,
                data={
                    "csrf_token": csrf_token,
                    "username": username,
                    "password": password,
                },
            )

        # 3. Confirm login succeeded — the site redirects to "/" and shows
        #    a "Logout" link only when authenticated.
        if "Logout" not in post_resp.text:
            return None

        cookies = dict(post_resp.cookies.items())
        if not cookies:
            return None

        from app.services.session_manager import save_session

        save_session(self.session_key, cookies)
        return cookies

    def is_login_gate(self, html: str) -> bool:
        """Detect an anonymous/logged-out page (login link present, no logout)."""
        return "Login" in html and "Logout" not in html
