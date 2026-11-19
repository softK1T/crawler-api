"""Offline tests; also runnable with python -m unittest discover -s tests -p test_proxy_geo_unit.py."""

import asyncio
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.proxy_geo import discover_exit_ip
from app.services.proxy_geo_policy import (
    apply_observation,
    country_code,
    parse_exit_ip,
    pin_proxy,
    probe_endpoint,
    public_ip,
    selection_matches,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC)


def proxy(**changes):
    values = {
        "id": uuid4(),
        "pool_id": uuid4(),
        "is_active": True,
        "cooldown_until": None,
        "country": "PL",
        "city": "Warsaw",
        "proxy_type": "datacenter",
        "geo_enabled": False,
        "declared_country": None,
        "detected_country": None,
        "exit_ip": None,
        "geo_status": "unmanaged",
        "geo_checked_at": None,
        "geo_next_check_at": None,
        "geo_last_error": None,
        "geo_attempted_at": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class GeoPolicyTests(unittest.TestCase):
    def test_country_normalization(self):
        self.assertEqual(country_code(" pl "), "PL")

    def test_invalid_country_rejected(self):
        for value in ("POL", "", "P1", "ПЛ", "P"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                country_code(value)

    def test_non_public_addresses_rejected(self):
        for value in (
            "127.0.0.1",
            "10.1.2.3",
            "169.254.169.254",
            "192.168.1.1",
            "100.64.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "224.0.0.1",
            "::ffff:127.0.0.1",
            "0.0.0.0",  # noqa: S104 -- rejection test input, not a listening socket
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_ip(value)

    def test_public_ip_accepted(self):
        self.assertEqual(public_ip("1.1.1.1"), "1.1.1.1")

    def test_endpoint_requires_public_https_ip(self):
        self.assertEqual(
            probe_endpoint("https://1.1.1.1/cdn-cgi/trace"), "https://1.1.1.1/cdn-cgi/trace"
        )
        for value in (
            "http://1.1.1.1/",
            "https://127.0.0.1/",
            "https://example.com/",
            "https://user:pass@1.1.1.1/",
            "https://1.1.1.1:8443/",
            "https://1.1.1.1/#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe_endpoint(value)

    def test_dns_result_is_pinned_and_credentials_preserved(self):
        self.assertEqual(
            pin_proxy("http://user:p%40ss@proxy.example:8080", ["8.8.8.8"]),
            "http://user:p%40ss@8.8.8.8:8080",
        )

    def test_mixed_public_private_dns_rejected(self):
        with self.assertRaises(ValueError):
            pin_proxy("http://proxy.example:80", ["8.8.8.8", "127.0.0.1"])

    def test_invalid_proxy_urls_rejected(self):
        for value in (
            "file:///etc/passwd",
            "http://1.1.1.1",
            "http://1.1.1.1:80/path",
            "http://1.1.1.1:80/?x=1",
            "http://1.1.1.1:80/#x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pin_proxy(value, ["1.1.1.1"])

    def test_ipv6_is_bracketed(self):
        self.assertEqual(
            pin_proxy("socks5://proxy.example:1080", ["2606:4700:4700::1111"]),
            "socks5://[2606:4700:4700::1111]:1080",
        )

    def test_exit_ip_parsing(self):
        self.assertEqual(parse_exit_ip(b"fl=123\nip=8.8.8.8\nloc=US\n"), "8.8.8.8")

    def test_bad_exit_responses_rejected(self):
        for body in (
            b"ip=127.0.0.1",
            b"<html>blocked</html>",
            b"ip=8.8.8.8\nip=1.1.1.1",
            b"x" * 16385,
        ):
            with self.subTest(body=body[:30]), self.assertRaises(ValueError):
                parse_exit_ip(body)

    def test_unmanaged_proxy_is_unchanged(self):
        self.assertTrue(selection_matches(proxy(), country="pl", now=NOW))

    def test_all_sticky_constraints(self):
        p = proxy()
        for filters in (
            {"pool_id": uuid4()},
            {"country": "DE"},
            {"city": "Berlin"},
            {"proxy_type": "residential"},
        ):
            with self.subTest(filters=filters):
                self.assertFalse(selection_matches(p, now=NOW, **filters))

    def test_inactive_or_cooling_proxy_excluded(self):
        self.assertFalse(selection_matches(proxy(is_active=False), now=NOW))
        self.assertFalse(
            selection_matches(
                proxy(cooldown_until=NOW + timedelta(seconds=1)),
                now=NOW,
            )
        )

    def test_pending_failed_and_unknown_excluded(self):
        for status in ("pending", "failed", "unknown"):
            with self.subTest(status=status):
                self.assertFalse(
                    selection_matches(
                        proxy(geo_enabled=True, geo_status=status),
                        now=NOW,
                    )
                )

    def test_verified_and_mismatch_require_freshness(self):
        for status in ("verified", "mismatch"):
            p = proxy(
                geo_enabled=True,
                geo_status=status,
                geo_checked_at=NOW,
                geo_next_check_at=NOW + timedelta(seconds=1),
            )
            self.assertTrue(selection_matches(p, now=NOW))
            self.assertFalse(selection_matches(p, now=NOW + timedelta(seconds=1)))

    def test_observation_updates_country_and_preserves_declaration(self):
        p = proxy()
        apply_observation(p, now=NOW, retry_s=60, ttl_s=3600, exit_ip="8.8.8.8", country="DE")
        self.assertEqual((p.country, p.detected_country, p.declared_country), ("DE", "DE", "PL"))
        self.assertEqual(p.geo_status, "mismatch")
        self.assertIsNone(p.city)

    def test_geo_does_not_activate_or_reclassify(self):
        p = proxy(is_active=False, country=None)
        apply_observation(p, now=NOW, retry_s=60, ttl_s=3600, exit_ip="8.8.8.8", country="PL")
        self.assertFalse(p.is_active)
        self.assertEqual(p.proxy_type, "datacenter")
        self.assertEqual(p.geo_status, "verified")

    def test_failure_preserves_last_success(self):
        p = proxy()
        apply_observation(p, now=NOW, retry_s=60, ttl_s=3600, exit_ip="8.8.8.8", country="PL")
        apply_observation(
            p, now=NOW + timedelta(seconds=1), retry_s=60, ttl_s=3600, error="TimeoutError"
        )
        self.assertEqual(p.country, "PL")
        self.assertEqual(p.exit_ip, "8.8.8.8")
        self.assertEqual(p.geo_checked_at, NOW)
        self.assertEqual(p.geo_status, "failed")

    def test_unknown_country_preserves_history_but_excludes_selection(self):
        p = proxy(geo_enabled=True, geo_checked_at=NOW)
        apply_observation(p, now=NOW, retry_s=60, ttl_s=3600, exit_ip="8.8.8.8", country=None)
        self.assertEqual(p.country, "PL")
        self.assertEqual(p.geo_status, "unknown")
        self.assertFalse(selection_matches(p, now=NOW))


class GeoProbeTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, status=200, chunks=(b"ip=8.8.8.8\n",), error=None):
        calls = []

        class Session:
            def __init__(self, **kwargs):
                calls.append(("session", kwargs))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                calls.append((url, kwargs))
                if error:
                    raise error
                for chunk in chunks:
                    kwargs["content_callback"](chunk)
                return SimpleNamespace(status_code=status)

        root = types.ModuleType("curl_cffi")
        requests = types.ModuleType("curl_cffi.requests")
        requests.__dict__["AsyncSession"] = Session
        root.__dict__["requests"] = requests
        return calls, {"curl_cffi": root, "curl_cffi.requests": requests}

    async def test_proxy_is_explicit_tls_verified_no_redirects_or_environment(self):
        calls, modules = self.transport()
        with patch.dict(sys.modules, modules):
            result = await discover_exit_ip(
                "http://8.8.4.4:8080",
                "https://1.1.1.1/cdn-cgi/trace",
                1,
            )
        self.assertEqual(result, "8.8.8.8")
        self.assertFalse(calls[0][1]["trust_env"])
        kwargs = calls[1][1]
        self.assertEqual(kwargs["proxy"], "http://8.8.4.4:8080")
        self.assertTrue(kwargs["verify"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(len(calls), 2)

    async def test_private_proxy_never_contacted(self):
        calls, modules = self.transport()
        with patch.dict(sys.modules, modules), self.assertRaises(ValueError):
            await discover_exit_ip("http://127.0.0.1:8080", "https://1.1.1.1/", 1)
        self.assertEqual(calls, [])

    async def test_dns_pinning(self):
        calls, modules = self.transport()
        loop = asyncio.get_running_loop()
        resolver = AsyncMock(return_value=[(2, 1, 6, "", ("8.8.4.4", 8080))])
        with patch.dict(sys.modules, modules), patch.object(loop, "getaddrinfo", resolver):
            await discover_exit_ip("http://proxy.example:8080", "https://1.1.1.1/", 1)
        self.assertEqual(calls[1][1]["proxy"], "http://8.8.4.4:8080")

    async def test_transport_failure_has_no_direct_retry(self):
        calls, modules = self.transport(error=TimeoutError())
        with patch.dict(sys.modules, modules), self.assertRaises(TimeoutError):
            await discover_exit_ip("http://8.8.4.4:8080", "https://1.1.1.1/", 1)
        self.assertEqual(len(calls), 2)

    async def test_redirect_rejected(self):
        _, modules = self.transport(status=302)
        with patch.dict(sys.modules, modules), self.assertRaises(ValueError):
            await discover_exit_ip("http://8.8.4.4:8080", "https://1.1.1.1/", 1)

    async def test_body_limit(self):
        _, modules = self.transport(chunks=(b"x" * 10000, b"y" * 10000))
        with patch.dict(sys.modules, modules), self.assertRaises(ValueError):
            await discover_exit_ip("http://8.8.4.4:8080", "https://1.1.1.1/", 1)


if __name__ == "__main__":
    unittest.main()
