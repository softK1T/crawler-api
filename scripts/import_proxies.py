#!/usr/bin/env python3
"""Import proxies from ``host:port:user:pass:country`` lines.

Usage::

    python scripts/import_proxies.py proxies.txt --tenant-id <UUID>

Each non-empty, non-comment line must contain exactly 5 colon-separated fields.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from app.core.db import AsyncSessionLocal
from app.models.proxy import Proxy


def parse_line(line: str, lineno: int) -> dict:
    parts = line.strip().split(":")
    if len(parts) != 5:
        raise SystemExit(f"line {lineno}: expected 5 fields, got {len(parts)}")
    host, port, user, password, country = parts
    return {
        "host": host,
        "port": int(port),
        "username": user,
        "password": password,
        "country": country.upper(),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Import proxies from host:port:user:pass:country lines"
    )
    ap.add_argument("file", type=Path, help="Path to proxy list file")
    ap.add_argument("--tenant-id", type=UUID, help="Pool UUID to assign proxies to")
    args = ap.parse_args()

    pool_id = args.tenant_id or uuid4()

    lines = args.file.read_text().splitlines()
    rows = [
        parse_line(ln, i) for i, ln in enumerate(lines, 1) if ln.strip() and not ln.startswith("#")
    ]

    if not rows:
        print("No proxy entries found in file.")
        return 1

    async with AsyncSessionLocal() as db:
        created = 0
        for item in rows:
            proxy_url = (
                f"http://{item['username']}:{item['password']}@{item['host']}:{item['port']}"
            )
            stmt = (
                insert(Proxy)
                .values(
                    pool_id=pool_id,
                    url=proxy_url,
                    country=item["country"],
                    health_score=1.0,
                    consecutive_failures=0,
                )
                .on_conflict_do_update(
                    index_elements=["url"],
                    set_={
                        "country": item["country"],
                        "health_score": 1.0,
                        "consecutive_failures": 0,
                        "cooldown_until": None,
                    },
                )
            )
            await db.execute(stmt)
            created += 1

        await db.commit()
        print(f"pool_id={pool_id} imported={created}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
