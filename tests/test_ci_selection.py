"""Tests for the lightweight CI fixture classifier."""

import unittest
from types import SimpleNamespace

import pytest

from scripts import ci_test_selection as selection


def item(path="tests/unit/test_policy.py", fixtures=(), marked=False):
    return SimpleNamespace(
        path=path,
        fixturenames=fixtures,
        get_closest_marker=lambda name: object() if marked else None,
    )


class CISelectionTests(unittest.TestCase):
    def test_pure_test_stays_fast(self):
        self.assertFalse(selection.requires_services(item()))

    def test_transitive_database_fixture_is_not_fast(self):
        self.assertTrue(selection.requires_services(item(fixtures=("limiter", "_redis_url"))))
        self.assertTrue(selection.requires_services(item(fixtures=("db_session",))))

    def test_directory_and_marker_are_respected(self):
        self.assertTrue(selection.requires_services(item(path="tests/integration/test_x.py")))
        self.assertTrue(selection.requires_services(item(marked=True)))

    def test_dynamic_service_fixture_fails_fast(self):
        request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: True))
        with self.assertRaises(pytest.UsageError):
            selection.pytest_fixture_setup(SimpleNamespace(argname="_postgres_dsn"), request)

    def test_default_mode_does_not_block_services(self):
        request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: False))
        selection.pytest_fixture_setup(SimpleNamespace(argname="_postgres_dsn"), request)


if __name__ == "__main__":
    unittest.main()
