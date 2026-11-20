import unittest
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from sqlalchemy.dialects import postgresql

from app.services import fetch_history
from app.services.fetch_history import (
    FetchFilterError,
    FetchFilters,
    build_search,
    decode_cursor,
    domain_candidates,
    encode_cursor,
    parse_outcomes,
    parse_status_codes,
    resolve_window,
    split_values,
)

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)


class WindowTests(unittest.TestCase):
    def test_default_window_is_the_last_24_hours(self) -> None:
        self.assertEqual(resolve_window(None, None, now=NOW), (NOW - timedelta(hours=24), NOW))

    def test_naive_values_are_read_as_utc(self) -> None:
        # Date-only query values such as from=2026-11-19 arrive without a timezone.
        since, until = resolve_window(
            datetime.fromisoformat("2026-11-19"), datetime.fromisoformat("2026-11-21"), now=NOW
        )
        self.assertEqual(since, datetime(2026, 11, 19, tzinfo=UTC))
        self.assertEqual(until, datetime(2026, 11, 21, tzinfo=UTC))

    def test_reversed_and_oversized_windows_are_rejected(self) -> None:
        with self.assertRaises(FetchFilterError):
            resolve_window(NOW, NOW - timedelta(hours=1), now=NOW)
        with self.assertRaises(FetchFilterError):
            resolve_window(NOW - timedelta(days=32), NOW, now=NOW)


class ParsingTests(unittest.TestCase):
    def test_split_values_drops_blanks_and_duplicates(self) -> None:
        found = split_values(" camoufox, ,playwright,camoufox ")
        self.assertEqual(found, ("camoufox", "playwright"))
        self.assertEqual(split_values(None), ())

    def test_domains_are_matched_as_typed_and_normalized(self) -> None:
        with mock.patch.object(fetch_history, "normalize_domain", lambda value: "mediaexpert.pl"):
            self.assertEqual(
                domain_candidates("WWW.MediaExpert.pl"), ("www.mediaexpert.pl", "mediaexpert.pl")
            )

    def test_unknown_outcome_and_bad_status_code_are_rejected(self) -> None:
        self.assertEqual(parse_outcomes("blocked,success"), ("blocked", "success"))
        with self.assertRaises(FetchFilterError):
            parse_outcomes("exploded")
        self.assertEqual(parse_status_codes("403, 429"), (403, 429))
        with self.assertRaises(FetchFilterError):
            parse_status_codes("4xx")

    def test_cursor_round_trip_and_garbage(self) -> None:
        self.assertEqual(decode_cursor(encode_cursor(NOW, "job|1")), (NOW, "job|1"))
        for garbage in ("not-base64!", "aGVsbG8="):
            with self.assertRaises(FetchFilterError):
                decode_cursor(garbage)


class QueryTests(unittest.TestCase):
    def _sql(self, **overrides: Any) -> str:
        filters = replace(FetchFilters(since=NOW - timedelta(days=1), until=NOW), **overrides)
        statement = build_search(uuid.uuid4(), filters, limit=50)
        return str(statement.compile(dialect=postgresql.dialect()))

    def test_query_is_scoped_bounded_and_one_row_per_job(self) -> None:
        sql = self._sql()
        self.assertIn("request_log.application_id =", sql)
        self.assertIn("request_log.requested_at >=", sql)
        self.assertIn("request_log.requested_at <", sql)
        self.assertIn("PARTITION BY request_log.job_id", sql)
        self.assertIn("attempts.rn =", sql)
        self.assertIn("LIMIT", sql)
        self.assertNotIn("engine_hit", sql)

    def test_engine_filter_matches_any_attempt_and_outcome_the_last(self) -> None:
        sql = self._sql(engines=("camoufox",), outcomes=("blocked",), domains=("mediaexpert.pl",))
        self.assertIn("attempts.engine_hit =", sql)
        self.assertIn("attempts.outcome IN", sql)
        self.assertIn("request_log.domain IN", sql)


if __name__ == "__main__":
    unittest.main()
