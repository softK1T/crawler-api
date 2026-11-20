"""Offline tests for request identifiers in archive responses."""

import asyncio
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


class ArchiveIdentifierTests(unittest.TestCase):
    def test_entry_schema_exposes_request_log_id(self) -> None:
        from app.schemas.archive import ArchiveEntryResponse

        request_log_id = uuid.uuid4()
        row = SimpleNamespace(
            id=uuid.uuid4(),
            url="https://example.pl/",
            warc_filename="a.warc.gz",
            offset=0,
            length=10,
            sha256="0" * 64,
            is_revisit=False,
            content_type="text/html",
            status_code=200,
            captured_at=datetime.now(UTC),
            request_log_id=request_log_id,
        )
        entry = ArchiveEntryResponse.model_validate(row)
        self.assertEqual(entry.request_log_id, request_log_id)
        self.assertIsNone(entry.job_id)

    def test_job_lookup_skips_the_database_without_ids(self) -> None:
        from app.api.v1.endpoints.archive import _job_ids

        db = mock.Mock()
        db.execute = mock.AsyncMock(side_effect=AssertionError("database must not be queried"))
        self.assertEqual(asyncio.run(_job_ids(db, [None])), {})

    def test_job_lookup_maps_attempts_to_jobs(self) -> None:
        from app.api.v1.endpoints.archive import _job_ids

        first, second = uuid.uuid4(), uuid.uuid4()
        db = mock.Mock()
        db.execute = mock.AsyncMock(return_value=[SimpleNamespace(id=first, job_id="job-1")])
        jobs = asyncio.run(_job_ids(db, [first, second, None]))
        self.assertEqual(jobs, {first: "job-1"})
        self.assertIsNone(jobs.get(None))
        db.execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
