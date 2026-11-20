"""Per-request header, cookie and User-Agent overrides.

Values are credentials: never log them or put them into error messages.
The active options travel in a ContextVar so fetchers pick them up without
widening the FetcherProtocol signature; arq runs every job in its own task.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

MAX_HEADERS = 40
MAX_COOKIES = 150
MAX_VALUE_LENGTH = 8192
MAX_TOTAL_BYTES = 32768

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_IMPERSONATE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_DEDICATED_FIELDS = {"user-agent": "user_agent", "cookie": "cookies"}
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "upgrade",
        "te",
        "trailer",
        "expect",
        "cookie2",
        "accept-encoding",
    }
)
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie"})


def _text(value: object, label: str, *, limit: int = MAX_VALUE_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if len(value) > limit:
        raise ValueError(f"{label} is too long")
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError(f"{label} must be printable ASCII")
    return value


def _put(target: dict[str, str], name: str, value: str) -> None:
    for existing in [key for key in target if key.lower() == name.lower()]:
        del target[existing]
    target[name] = value


@dataclass(frozen=True, slots=True, repr=False)
class RequestOptions:
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    user_agent: str | None = None
    impersonate: str | None = None

    def __bool__(self) -> bool:
        return bool(self.headers or self.cookies or self.user_agent or self.impersonate)

    def __repr__(self) -> str:
        return (
            f"RequestOptions(headers={sorted(self.headers)}, cookies={len(self.cookies)}, "
            f"user_agent={self.user_agent is not None}, impersonate={self.impersonate!r})"
        )

    @classmethod
    def from_options(cls, options: Mapping[str, Any] | None) -> RequestOptions:
        """Validate the request-level keys of a job ``options`` mapping."""
        options = options or {}
        raw_headers = options.get("headers") or {}
        raw_cookies = options.get("cookies") or {}
        if not isinstance(raw_headers, Mapping) or not isinstance(raw_cookies, Mapping):
            raise ValueError("headers and cookies must be objects")
        if len(raw_headers) > MAX_HEADERS:
            raise ValueError(f"at most {MAX_HEADERS} headers are allowed")
        if len(raw_cookies) > MAX_COOKIES:
            raise ValueError(f"at most {MAX_COOKIES} cookies are allowed")

        headers: dict[str, str] = {}
        seen: set[str] = set()
        for name, value in raw_headers.items():
            if not isinstance(name, str) or len(name) > 128 or not _TOKEN.fullmatch(name):
                raise ValueError("invalid header name")
            lowered = name.lower()
            if lowered in _DEDICATED_FIELDS:
                raise ValueError(
                    f"use the {_DEDICATED_FIELDS[lowered]} field instead of the {name} header"
                )
            if lowered in _FORBIDDEN_HEADERS or lowered.startswith("proxy-"):
                raise ValueError(f"header {name} cannot be set per request")
            if lowered in seen:
                raise ValueError(f"header {name} is repeated")
            seen.add(lowered)
            headers[name] = _text(value, f"header {name}")

        cookies: dict[str, str] = {}
        for name, value in raw_cookies.items():
            if not isinstance(name, str) or len(name) > 256 or not _TOKEN.fullmatch(name):
                raise ValueError("invalid cookie name")
            text = _text(value, f"cookie {name}")
            if ";" in text:
                raise ValueError(f"cookie {name} must not contain a semicolon")
            cookies[name] = text

        user_agent = options.get("user_agent")
        if user_agent is not None:
            user_agent = _text(user_agent, "user_agent", limit=512)
            if not user_agent.strip():
                raise ValueError("user_agent must not be empty")

        impersonate = options.get("impersonate")
        if impersonate is not None and (
            not isinstance(impersonate, str) or not _IMPERSONATE.fullmatch(impersonate)
        ):
            raise ValueError("impersonate must be a curl_cffi browser profile such as chrome124")

        total = sum(len(name) + len(value) for name, value in headers.items())
        total += sum(len(name) + len(value) for name, value in cookies.items())
        total += len(user_agent or "")
        if total > MAX_TOTAL_BYTES:
            raise ValueError("request options are too large")
        return cls(headers=headers, cookies=cookies, user_agent=user_agent, impersonate=impersonate)

    def cookie_header(self) -> str | None:
        if not self.cookies:
            return None
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())

    def apply(
        self, base: Mapping[str, str] | None, *, send_sensitive: bool = True
    ) -> dict[str, str]:
        """Merge over ``base``; cookies and Authorization only when ``send_sensitive``."""
        merged = dict(base or {})
        if self.user_agent:
            _put(merged, "User-Agent", self.user_agent)
        for name, value in self.headers.items():
            if send_sensitive or name.lower() not in _SENSITIVE_HEADERS:
                _put(merged, name, value)
        cookie = self.cookie_header()
        if cookie and send_sensitive:
            _put(merged, "Cookie", cookie)
        return merged


def same_site(first_url: str, other_url: str) -> bool:
    """True when ``other_url`` stays on the registered domain without a TLS downgrade."""
    from app.services.policy_resolver import normalize_domain

    first, other = urlsplit(first_url), urlsplit(other_url)
    if not first.hostname or not other.hostname:
        return False
    if first.scheme == "https" and other.scheme != "https":
        return False
    return normalize_domain(first.hostname) == normalize_domain(other.hostname)


def headers_for_hop(
    base: Mapping[str, str] | None,
    options: RequestOptions | None,
    first_url: str,
    hop_url: str,
) -> dict[str, str]:
    """Headers for one redirect hop; credentials never follow a cross-site redirect."""
    if not options:
        return dict(base or {})
    return options.apply(base, send_sensitive=same_site(first_url, hop_url))


def without_user_agent(
    base: Mapping[str, str] | None, options: RequestOptions | None
) -> dict[str, str]:
    """Drop the header-level UA when the browser context already carries the requested one."""
    if options and options.user_agent:
        return {name: value for name, value in (base or {}).items() if name.lower() != "user-agent"}
    return dict(base or {})


_current: ContextVar[RequestOptions | None] = ContextVar("crawler_request_options", default=None)


def set_request_options(options: RequestOptions | None) -> None:
    _current.set(options)


def clear_request_options() -> None:
    _current.set(None)


def current_request_options() -> RequestOptions | None:
    return _current.get()
