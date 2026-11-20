"""Offline tests for per-request options and the domain-policy switch."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from app.services.fetchers.request_options import (
    RequestOptions,
    clear_request_options,
    current_request_options,
    headers_for_hop,
    same_site,
    set_request_options,
    without_user_agent,
)

SECRET = "s3cr3t-session-value"


class RequestOptionsTests(unittest.TestCase):
    def test_parse_and_merge_over_base_headers(self) -> None:
        options = RequestOptions.from_options(
            {
                "headers": {"Referer": "https://www.google.com/", "accept-language": "pl-PL"},
                "cookies": {"a": "1", "b": "x=y"},
                "user_agent": "UA/1.0",
            }
        )
        merged = options.apply({"User-Agent": "old", "Accept-Language": "en", "Accept": "*/*"})
        self.assertEqual(merged["User-Agent"], "UA/1.0")
        self.assertNotIn("Accept-Language", merged)
        self.assertEqual(merged["accept-language"], "pl-PL")
        self.assertEqual(merged["Cookie"], "a=1; b=x=y")
        self.assertEqual(merged["Accept"], "*/*")

    def test_empty_options_are_falsy_and_keep_base(self) -> None:
        self.assertFalse(RequestOptions.from_options({}))
        self.assertFalse(RequestOptions.from_options(None))
        base = {"Accept": "*/*"}
        hop = headers_for_hop(base, None, "https://example.pl/", "https://other.com/")
        self.assertEqual(hop, base)
        self.assertIsNot(hop, base)
        self.assertEqual(
            headers_for_hop(None, None, "https://example.pl/", "https://example.pl/"), {}
        )

    def test_credentials_do_not_follow_cross_site_redirects(self) -> None:
        options = RequestOptions.from_options(
            {"headers": {"Authorization": SECRET, "X-Test": "1"}, "cookies": {"sid": SECRET}}
        )
        same = headers_for_hop({}, options, "https://www.example.pl/a", "https://example.pl/b")
        self.assertEqual(same["Cookie"], f"sid={SECRET}")
        self.assertEqual(same["Authorization"], SECRET)
        cross = headers_for_hop({}, options, "https://www.example.pl/a", "https://cdn.other.com/b")
        self.assertNotIn("Cookie", cross)
        self.assertNotIn("Authorization", cross)
        self.assertEqual(cross["X-Test"], "1")

    def test_same_site_rules(self) -> None:
        self.assertTrue(same_site("https://www.example.pl/", "https://m.example.pl/x"))
        self.assertFalse(same_site("https://example.pl/", "https://example.com/"))
        self.assertFalse(same_site("https://example.pl/", "http://example.pl/"))
        self.assertFalse(same_site("https://example.pl/", "not a url"))

    def test_invalid_input_is_rejected(self) -> None:
        bad: list[dict[str, object]] = [
            {"headers": {"Host": "x"}},
            {"headers": {"Cookie": "a=b"}},
            {"headers": {"User-Agent": "x"}},
            {"headers": {"Proxy-Authorization": "x"}},
            {"headers": {"Accept-Encoding": "zstd"}},
            {"headers": {"X-A": "line\r\nInjected: 1"}},
            {"headers": {"Bad Name": "x"}},
            {"headers": {"X-A": "1", "x-a": "2"}},
            {"headers": ["x"]},
            {"cookies": {"a": "1;b=2"}},
            {"cookies": {"a": 1}},
            {"user_agent": " "},
            {"impersonate": "Chrome 120"},
            {"headers": {f"X-{index}": "v" * 8000 for index in range(5)}},
        ]
        for options in bad:
            with self.subTest(keys=sorted(options)), self.assertRaises(ValueError):
                RequestOptions.from_options(options)

    def test_values_never_leak_into_repr_or_errors(self) -> None:
        options = RequestOptions.from_options(
            {"cookies": {"sid": SECRET}, "headers": {"Authorization": SECRET}}
        )
        self.assertNotIn(SECRET, repr(options))
        with self.assertRaises(ValueError) as caught:
            RequestOptions.from_options({"cookies": {"sid": SECRET + ";"}})
        self.assertNotIn(SECRET, str(caught.exception))

    def test_without_user_agent(self) -> None:
        base = {"User-Agent": "old", "Accept": "*/*"}
        explicit = RequestOptions.from_options({"user_agent": "UA/1.0"})
        self.assertEqual(without_user_agent(base, explicit), {"Accept": "*/*"})
        self.assertEqual(without_user_agent(base, None), base)

    def test_context_variable_is_set_and_cleared(self) -> None:
        options = RequestOptions.from_options({"user_agent": "UA/1.0"})
        set_request_options(options)
        self.assertIs(current_request_options(), options)
        clear_request_options()
        self.assertIsNone(current_request_options())


class PolicySwitchTests(unittest.TestCase):
    def test_disabled_policies_resolve_to_none_without_database(self) -> None:
        from app.core.config import settings
        from app.services.policy_resolver import resolve_policy

        with mock.patch.object(settings, "enable_domain_policies", False):
            result = asyncio.run(resolve_policy("https://www.example.com/", mock.Mock()))
        self.assertIsNone(result)


class UsePolicyFieldTests(unittest.TestCase):
    def test_job_schema_accepts_use_policy(self) -> None:
        from app.schemas.job import JobCreate

        default = JobCreate.model_validate({"url": "https://www.example.com/"})
        self.assertIsNone(default.use_policy)
        disabled = JobCreate.model_validate(
            {"url": "https://www.example.com/", "use_policy": False}
        )
        self.assertIs(disabled.use_policy, False)


class CredentialScopeTests(unittest.TestCase):
    def test_carries_credentials(self) -> None:
        from app.services.fetchers.request_options import carries_credentials

        with_cookies = RequestOptions.from_options({"cookies": {"a": "1"}})
        with_auth = RequestOptions.from_options({"headers": {"authorization": "x"}})
        plain = RequestOptions.from_options({"headers": {"Referer": "x"}})
        self.assertTrue(carries_credentials(with_cookies))
        self.assertTrue(carries_credentials(with_auth))
        self.assertFalse(carries_credentials(plain))

    def test_spans_multiple_sites(self) -> None:
        from app.services.fetchers.request_options import spans_multiple_sites

        same = ["https://www.example.pl/a", "https://m.example.pl/b"]
        mixed = ["https://example.pl/", "https://example.com/"]
        self.assertFalse(spans_multiple_sites(same))
        self.assertTrue(spans_multiple_sites(mixed))
        self.assertFalse(spans_multiple_sites([]))

    def test_use_policy_must_be_boolean(self) -> None:
        RequestOptions.from_options({"use_policy": False})
        with self.assertRaises(ValueError):
            RequestOptions.from_options({"use_policy": "false"})


class CurlIdentityTests(unittest.TestCase):
    def test_profile_and_user_agent_selection(self) -> None:
        from app.services.fetchers.request_options import curl_identity

        custom_ua = RequestOptions.from_options({"user_agent": "UA/1.0"})
        profile = RequestOptions.from_options({"impersonate": "chrome124"})
        both = RequestOptions.from_options({"impersonate": "chrome124", "user_agent": "UA/1.0"})
        self.assertEqual(curl_identity(None, "chrome120", "UA120"), ("chrome120", "UA120"))
        self.assertEqual(curl_identity(custom_ua, "chrome120", "UA120"), ("chrome120", "UA/1.0"))
        self.assertEqual(curl_identity(profile, "chrome120", "UA120"), ("chrome124", None))
        self.assertEqual(curl_identity(both, "chrome120", "UA120"), ("chrome124", "UA/1.0"))


class NavigationHeaderTests(unittest.TestCase):
    @staticmethod
    def _request(url: str, *, navigation: bool = True, top: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            url=url,
            headers={"accept": "*/*", "referer": "old"},
            frame=SimpleNamespace(parent_frame=None if top else object()),
            is_navigation_request=lambda: navigation,
        )

    def test_headers_reach_only_same_site_top_level_navigations(self) -> None:
        from app.services.fetchers.browser_guard import _navigation_kwargs

        custom = {"Referer": "https://www.google.com/"}
        site = "https://www.example.pl/"
        merged = _navigation_kwargs(self._request("https://example.pl/x"), custom, site)
        self.assertEqual(
            merged, {"headers": {"accept": "*/*", "referer": "https://www.google.com/"}}
        )
        subresource = self._request("https://example.pl/x", navigation=False)
        iframe = self._request("https://example.pl/x", top=False)
        foreign = self._request("https://other.com/x")
        self.assertEqual(_navigation_kwargs(subresource, custom, site), {})
        self.assertEqual(_navigation_kwargs(iframe, custom, site), {})
        self.assertEqual(_navigation_kwargs(foreign, custom, site), {})
        self.assertEqual(_navigation_kwargs(self._request("https://example.pl/x"), None, site), {})


if __name__ == "__main__":
    unittest.main()
