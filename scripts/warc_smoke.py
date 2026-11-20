"""Live S3/DB/archive-API smoke; only synthetic captures, no external fetches."""

import asyncio
import base64
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.db import AsyncSessionLocal, engine
from app.services.warc import storage as module

PROOF = Path("/var/lib/crawler/warc-spool/smoke-proof")
BODY = b"crawler archival smoke: exact body survives S3 and worker restart\n"


async def verify(ids: list[str]) -> None:
    import httpx

    key = Path("/run/crawler-secrets/admin.key").read_text().strip()  # noqa: ASYNC240
    async with httpx.AsyncClient(base_url="http://api:8000", headers={"X-API-Key": key}) as client:
        for entry_id in ids:
            response = await client.get(f"/v1/archive/{entry_id}")
            response.raise_for_status()
            payload = response.json()
            if base64.b64decode(payload["body_b64"]) != BODY:
                raise RuntimeError("Archive API returned different body bytes")
            if payload["is_revisit"]:
                raise RuntimeError("New captures must be independently readable responses")


async def run() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        ids = json.loads(await asyncio.to_thread(PROOF.read_text))
        await verify(ids)
        print("Persisted S3 objects and archive API survived service restart")
        return
    settings.warc_prefix = f"smoke/{uuid4().hex}"
    settings.warc_dlq_dir = str(PROOF.parent / f"smoke-{uuid4().hex}")
    storage = await module.create_warc_storage(settings)
    ids = []
    result = SimpleNamespace(
        url=f"https://example.invalid/{uuid4().hex}",
        status_code=200,
        body=BODY,
        headers={"content-type": "text/plain"},
        raw_headers={"content-type": "text/plain", "content-encoding": "gzip"},
    )
    try:
        # Same URL/body twice: no broken revisit references and no wrong-record reads.
        for _ in range(2):
            async with AsyncSessionLocal() as db:
                row = await storage.archive(
                    fetch_result=result, request_log_id=None, db=db, warc_body=gzip.compress(BODY)
                )
                ids.append(str(row.id))
        for target in ("s3", "db"):
            failing = AsyncMock(side_effect=ConnectionError(f"synthetic {target} outage"))
            context = (
                patch.object(storage._s3, "put_object", failing)
                if target == "s3"
                else patch.object(module, "_persist_index", failing)
            )
            with context:
                async with AsyncSessionLocal() as db:
                    try:
                        await storage.archive(
                            fetch_result=result,
                            request_log_id=None,
                            db=db,
                            warc_body=gzip.compress(BODY),
                        )
                    except ConnectionError:
                        pass
                    else:
                        raise RuntimeError("Outage did not fail the capture")
            pending = await asyncio.to_thread(storage._spool.pending)
            if len(pending) != 1:
                raise RuntimeError("Failed capture was not durably retained")
            entry, _ = await asyncio.to_thread(storage._spool.read, pending[0])
            ids.append(entry["index"]["id"])
            await storage.shutdown_flush()
            storage = await module.create_warc_storage(settings)
            if await storage.retry_uploads() != 1:
                raise RuntimeError("Restart recovery did not deliver capture and index")
            if await storage.retry_uploads() != 0:
                raise RuntimeError("Replay was not idempotent")
        await verify(ids)
        await asyncio.to_thread(PROOF.write_text, json.dumps(ids))
        print("WARC gzip/readback, duplicate captures, S3/DB failure and restart replay passed")
    finally:
        await storage.shutdown_flush()


async def main() -> None:
    try:
        await run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
