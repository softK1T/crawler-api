"""Offline import, activation and country-filter tests."""

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.services.proxy_candidates import (
    candidate_matches,
    normalize_candidate,
    record_check,
    trace_country,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC)


def candidate(**updates):
    values = {
        "pool_id": uuid4(),
        "is_active": False,
        "cooldown_until": None,
        "country": None,
        "city": None,
        "proxy_type": "unknown",
        "check_managed": True,
        "check_auto_activate": True,
        "check_status": "pending",
        "check_last_at": None,
        "check_due_at": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


class CandidateTests(unittest.TestCase):
    def test_public_ip_urls(self):
        for protocol in ("http", "https", "socks4", "socks5", "socks5h"):
            value = f"{protocol}://8.8.8.8:1080"
            self.assertEqual(normalize_candidate(value), value)

    def test_private_hostname_path_and_missing_scheme_rejected(self):
        for value in (
            "http://127.0.0.1:80",
            "http://localhost:80",
            "http://10.0.0.1:80",
            "http://8.8.8.8:80/path",
            "8.8.8.8:8080",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_candidate(value)

    def test_country_is_client_location_not_colo(self):
        self.assertEqual(trace_country(b"ip=8.8.8.8\nloc=PL\ncolo=FRA\n"), "PL")

    def test_missing_invalid_duplicate_country(self):
        for suffix in (b"", b"loc=XX", b"loc=T1", b"loc=PL\nloc=DE"):
            self.assertIsNone(trace_country(b"ip=8.8.8.8\n" + suffix))

    def test_success_activates_and_tags(self):
        p = candidate()
        record_check(
            p, now=NOW, ttl_s=3600, retry_s=60,
            exit_ip="8.8.4.4", country="PL", source="cloudflare-trace",
        )
        self.assertTrue(p.is_active)
        self.assertEqual(p.country, "PL")
        self.assertEqual(p.proxy_type, "unknown")
        self.assertTrue(candidate_matches(p, country="PL", now=NOW))
        self.assertFalse(candidate_matches(p, country="DE", now=NOW))

    def test_unknown_country_active_only_without_country_filter(self):
        p = candidate(country="DE", city="Berlin")
        record_check(
            p, now=NOW, ttl_s=3600, retry_s=60, exit_ip="8.8.4.4", country=None,
        )
        self.assertTrue(p.is_active)
        self.assertIsNone(p.country)
        self.assertIsNone(p.city)
        self.assertTrue(candidate_matches(p, now=NOW))
        self.assertFalse(candidate_matches(p, country="PL", now=NOW))

    def test_expired_candidate_excluded(self):
        p = candidate()
        record_check(p, now=NOW, ttl_s=60, retry_s=60, exit_ip="8.8.4.4")
        self.assertFalse(candidate_matches(p, now=NOW + timedelta(seconds=60)))

    def test_failure_disables_and_retains_last_country(self):
        p = candidate(is_active=True, country="PL")
        record_check(p, now=NOW, ttl_s=60, retry_s=60, error="TimeoutError")
        self.assertFalse(p.is_active)
        self.assertEqual(p.country, "PL")
        self.assertEqual(p.check_error, "TimeoutError")
        self.assertEqual(p.check_status, "failed")

    def test_paused_candidate_never_reactivated(self):
        p = candidate(check_auto_activate=False, check_status="paused")
        record_check(
            p, now=NOW, ttl_s=60, retry_s=60, exit_ip="8.8.4.4", country="PL",
        )
        self.assertFalse(p.is_active)
        self.assertEqual(p.check_status, "paused")

    def test_unmanaged_proxy_never_modified(self):
        p = candidate(check_managed=False)
        record_check(
            p, now=NOW, ttl_s=60, retry_s=60, exit_ip="8.8.4.4", country="PL",
        )
        self.assertFalse(p.is_active)
        self.assertIsNone(p.country)

    def test_pool_and_type_constraints(self):
        p = candidate()
        record_check(
            p, now=NOW, ttl_s=60, retry_s=60, exit_ip="8.8.4.4", country="PL",
        )
        self.assertFalse(candidate_matches(p, pool_id=uuid4(), now=NOW))
        self.assertFalse(candidate_matches(p, proxy_type="datacenter", now=NOW))

    def test_geolocation_failure_does_not_fail_https(self):
        p = candidate()
        record_check(
            p, now=NOW, ttl_s=60, retry_s=60, exit_ip="8.8.4.4",
            geo_error="LookupError",
        )
        self.assertTrue(p.is_active)
        self.assertEqual(p.check_error, "LookupError")


if __name__ == "__main__":
    unittest.main()
