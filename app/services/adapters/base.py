from abc import ABC, abstractmethod
from typing import Optional, Dict


class SiteAdapter(ABC):
    """
    Abstract base for per-site login + session management.
    Each site subclass implements login() and is_login_gate().
    """

    # Must be defined by subclass
    session_key: str
    login_url: str

    def __init__(self, url: str = ""):
        self.target_url = url

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def login(
        self,
        username: str,
        password: str,
        proxy_url: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """
        Perform browser login, return cookies dict on success.
        Must also call save_session(self.session_key, cookies).
        """
        ...

    @abstractmethod
    def is_login_gate(self, html: str) -> bool:
        """Return True if the page is a login wall / auth redirect."""
        ...

    # ------------------------------------------------------------------
    # Shared helpers (do not override unless needed)
    # ------------------------------------------------------------------

    def get_cookies(self) -> Optional[Dict[str, str]]:
        """Load cookies from Redis. None if session expired/missing."""
        from app.services.session_manager import load_session
        return load_session(self.session_key)

    def has_active_session(self) -> bool:
        return self.get_cookies() is not None

    def clear_session(self) -> None:
        from app.services.session_manager import delete_session
        delete_session(self.session_key)

    def cookie_header(self) -> Optional[str]:
        """Return Cookie header string for use in static/stealth crawlers."""
        cookies = self.get_cookies()
        if not cookies:
            return None
        return "; ".join(f"{k}={v}" for k, v in cookies.items())
