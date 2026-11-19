"""Offline journal regression tests; no app imports, services or credentials."""

import asyncio
import json
import runpy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

_STORAGE = Path(__file__).parents[1] / "app/services/warc/storage.py"
_MODULE = runpy.run_path(str(_STORAGE))
Spool = _MODULE["WarcSpool"]
Storage = _MODULE["WarcStorage"]


class WarcSpoolTests(unittest.TestCase):
    def test_roundtrip_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Spool(directory, 10000)
            path = first.stage("bucket", {"id": "one"}, b"archive bytes")
            recovered = Spool(directory, 10000)
            entry, body = recovered.read(path)
            self.assertEqual(entry["bucket"], "bucket")
            self.assertEqual(body, b"archive bytes")
            self.assertEqual(recovered.pending(), [path])
            recovered.remove(path)
            self.assertEqual(recovered.pending(), [])

    def test_capacity_never_evicts(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Spool(directory, 250)
            path = spool.stage("bucket", {"id": "one"}, b"a")
            with self.assertRaises(OSError):
                spool.stage("bucket", {"id": "two"}, b"b" * 250)
            self.assertEqual(spool.read(path)[1], b"a")

    def test_corruption_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Spool(directory, 10000)
            path = spool.stage("bucket", {"id": "one"}, b"a")
            entry = json.loads(path.read_text())
            entry["sha256"] = "bad"
            path.write_text(json.dumps(entry))
            with self.assertRaises(ValueError):
                spool.read(path)
            self.assertTrue(path.exists())

    def test_failed_rename_preserves_existing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Spool(directory, 10000)
            path = spool.stage("bucket", {"id": "one"}, b"a")
            with (
                patch("os.replace", side_effect=OSError("disk failure")),
                self.assertRaises(OSError),
            ):
                spool.stage("bucket", {"id": "two"}, b"b")
            self.assertEqual(spool.pending(), [path])
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_concurrent_writers_obey_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Spool(directory, 250)

            def stage(number):
                try:
                    spool.stage("bucket", {"id": str(number)}, b"a")
                    return True
                except OSError:
                    return False

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(stage, range(8)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(len(spool.pending()), 1)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def storage(self, directory, client):
        @asynccontextmanager
        async def database():
            yield None

        storage = Storage.__new__(Storage)
        storage._s3 = client
        storage._bucket = "bucket"
        storage._spool = Spool(directory, 10000)
        storage._db_factory = database
        storage._client_context = None
        storage._lock = asyncio.Lock()
        return storage

    async def check_recovery(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            client = unittest.mock.Mock()
            client.put_object = AsyncMock()
            persist = AsyncMock(return_value="indexed")
            if failure == "s3":
                client.put_object.side_effect = ConnectionError("offline")
            elif failure == "cancel":
                client.put_object.side_effect = asyncio.CancelledError()
            else:
                persist.side_effect = ConnectionError("database offline")
            metadata = {"id": "stable-id", "warc_filename": "warc/stable.warc.gz"}
            with patch.dict(
                Storage.archive.__globals__,
                {"_capture": lambda *args: (metadata, b"warc"), "_persist_index": persist},
            ):
                first = self.storage(directory, client)
                expected = asyncio.CancelledError if failure == "cancel" else ConnectionError
                with self.assertRaises(expected):
                    await first.archive(fetch_result=None, request_log_id=None, db=None)
                self.assertEqual(len(first._spool.pending()), 1)
                client.put_object.side_effect = None
                persist.side_effect = None
                recovered = self.storage(directory, client)
                self.assertEqual(await recovered.retry_uploads(), 1)
                self.assertEqual(persist.call_args.args[1], metadata)
                self.assertEqual(await recovered.retry_uploads(), 0)
                self.assertEqual(recovered._spool.pending(), [])

    async def test_s3_failure_retains_and_recovers(self):
        await self.check_recovery("s3")

    async def test_index_failure_retains_and_recovers(self):
        await self.check_recovery("db")

    async def test_cancellation_retains_and_recovers(self):
        await self.check_recovery("cancel")


if __name__ == "__main__":
    unittest.main()
