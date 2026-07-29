"""arq cron task — periodic Webshare proxy list sync with DB reconciliation."""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


async def sync_proxies(ctx: dict) -> None:
    """Fetch the Webshare proxy list and reconcile the DB.

    - Upserts proxies by (pool_id, url) — does not duplicate on re-sync.
    - Deactivates rows absent from the provider response (is_active=False);
      never hard-deletes: health history and request_log FKs must survive.
    - Leaves health_score / cooldown untouched for existing rows.
    """
    if not settings.webshare_api_key:
        logger.info("sync_proxies: WEBSHARE_API_KEY not set — skipping")
        return

    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import text

    db_factory = ctx["db_factory"]

    # Fetch proxy list from Webshare.
    from app.services.webshare_sync import sync_webshare_to_file

    try:
        count = sync_webshare_to_file(
            api_key=settings.webshare_api_key,
            output_path=settings.webshare_proxy_file,
        )
    except Exception as exc:
        logger.error("sync_proxies: Webshare fetch failed: %s", exc)
        return

    if count == 0:
        logger.info("sync_proxies: no proxies returned from Webshare")
        return

    # Parse the downloaded proxy file.
    new_proxies: dict[str, dict] = {}
    try:
        with open(settings.webshare_proxy_file) as fh:  # noqa: ASYNC230
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                url = f"{parts[0]}:{parts[1]}"
                user = parts[2] if len(parts) > 2 else ""
                pwd = parts[3] if len(parts) > 3 else ""
                country = parts[4] if len(parts) > 4 else "unknown"
                new_proxies[url] = {"user": user, "password": pwd, "country": country}
    except OSError as exc:
        logger.error("sync_proxies: cannot read proxy file: %s", exc)
        return

    now = datetime.now(UTC)
    added = 0
    updated = 0
    deactivated = 0

    async with db_factory() as db:
        # Upsert proxies present in the response.
        for url, data in new_proxies.items():
            result = await db.execute(
                text("SELECT id, is_active FROM proxies WHERE url = :url"),
                {"url": url},
            )
            row = result.first()
            if row is None:
                await db.execute(
                    text(
                        "INSERT INTO proxies (id, pool_id, url, username, password, "
                        "country, is_active, health_score, created_at, updated_at) "
                        "VALUES (:id, :pool_id, :url, :user, :pwd, :country, "
                        "true, 1.0, :now, :now)"
                    ),
                    {
                        "id": str(uuid4()),
                        "pool_id": str(uuid4()),
                        "url": url,
                        "user": data["user"],
                        "pwd": data["password"],
                        "country": data["country"],
                        "now": now,
                    },
                )
                added += 1
            elif not row.is_active:
                await db.execute(
                    text(
                        "UPDATE proxies SET is_active = true, username = :user, "
                        "password = :pwd, country = :country, updated_at = :now "
                        "WHERE url = :url"
                    ),
                    {
                        "user": data["user"],
                        "pwd": data["password"],
                        "country": data["country"],
                        "now": now,
                        "url": url,
                    },
                )
                updated += 1

        # Deactivate proxies no longer in the provider list.
        if new_proxies:
            urls = list(new_proxies.keys())
            result = await db.execute(
                text(
                    "UPDATE proxies SET is_active = false, updated_at = :now "
                    "WHERE url NOT IN :urls AND is_active = true"
                ),
                {"now": now, "urls": tuple(urls)},
            )
            deactivated = result.rowcount or 0

        await db.commit()

    logger.info(
        "sync_proxies: done added=%d updated=%d deactivated=%d total_fetched=%d",
        added,
        updated,
        deactivated,
        count,
    )
