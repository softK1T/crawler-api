"""Offline tests for IPv6 address forms in the outbound address check.

No lookup happens here: the resolver is stubbed where a hostname is used.
"""

import asyncio
import socket
import unittest
from unittest import mock

from app.core.ssrf_guard import _is_ip_blocked

# Bound at import time: conftest replaces the guard with a no-op for every test.
from app.core.url_guard import (
    DNSResolutionError,
    URLGuardError,
    validate_url_async,
    validate_url_sync,
)

GUARDS = {
    "async": lambda url: asyncio.run(validate_url_async(url)),
    "sync": validate_url_sync,
}


class Ipv6AddressFormTests(unittest.TestCase):
    def test_ipv4_mapped_address_in_a_blocked_range_is_blocked(self) -> None:
        for ip in (
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:10.0.0.5",
            "::ffff:a00:5",
            "0:0:0:0:0:ffff:10.0.0.5",
        ):
            with self.subTest(ip=ip):
                self.assertTrue(_is_ip_blocked(ip))

    def test_unspecified_and_link_local_addresses_are_blocked(self) -> None:
        for ip in ("::", "fe80::1", "fe80::1%eth0"):
            with self.subTest(ip=ip):
                self.assertTrue(_is_ip_blocked(ip))

    def test_public_addresses_stay_allowed(self) -> None:
        for ip in ("::ffff:8.8.8.8", "2606:4700:4700::1111"):
            with self.subTest(ip=ip):
                self.assertFalse(_is_ip_blocked(ip))

    def test_url_with_such_a_literal_address_is_rejected(self) -> None:
        for host in ("::ffff:127.0.0.1", "::", "fe80::1"):
            for name, guard in GUARDS.items():
                with self.subTest(host=host, guard=name):
                    with self.assertRaises(URLGuardError) as caught:
                        guard(f"http://[{host}]/")
                    self.assertNotIsInstance(caught.exception, DNSResolutionError)

    def test_hostname_resolving_to_a_mapped_address_is_rejected(self) -> None:
        answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:10.0.0.5", 0, 0, 0))]
        for name, guard in GUARDS.items():
            with (
                self.subTest(guard=name),
                mock.patch("socket.getaddrinfo", return_value=answer),
            ):
                with self.assertRaises(URLGuardError) as caught:
                    guard("http://www.example.com/")
                self.assertNotIsInstance(caught.exception, DNSResolutionError)


if __name__ == "__main__":
    unittest.main()
