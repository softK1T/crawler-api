from app.services.adapters.base import SiteAdapter


class ExampleSiteAdapter(SiteAdapter):
    """Reference implementation for login-gated sites.

    This adapter is intentionally simple and deterministic: it exists to make
    Stage 8 end-to-end wiring concrete and testable. Real site adapters should
    replace this file with domain-specific browser automation.
    """

    session_key = "example.com"
    login_url = "https://example.com/login"

    async def login(
        self,
        username: str,
        password: str,
        proxy_url: str | None = None,
    ) -> dict[str, str] | None:
        if not username or not password:
            return None
        cookies = {
            "sessionid": f"example-{username}",
            "auth": "1",
        }
        from app.services.session_manager import save_session

        save_session(self.session_key, cookies)
        return cookies

    def is_login_gate(self, html: str) -> bool:
        sample = html.lower()
        return "login" in sample or "sign in" in sample or "/login" in sample
