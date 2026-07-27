"""HMAC-signed callback delivery to caller-provided webhook URLs."""

import hmac
import logging
from hashlib import sha256

import httpx

logger = logging.getLogger(__name__)

HMAC_HEADER = "X-Crawler-Signature"
HMAC_ALGO = "sha256"


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Return ``sha256=<hex_digest>`` using HMAC-SHA256."""
    digest = hmac.new(secret.encode(), payload_bytes, sha256).hexdigest()
    return f"{HMAC_ALGO}={digest}"


async def deliver_callback(
    callback_url: str,
    payload,
    secret: str,
    timeout_s: float = 10.0,
    max_retries: int = 3,
) -> None:
    """POST the payload to *callback_url* with HMAC signature header.

    Validates the URL against SSRF before delivery.  Retries on non-2xx
    or connection errors with exponential backoff (1s, 2s, 4s).  Never
    raises — failures are logged as warnings.
    """
    from app.core.url_guard import URLGuardError, validate_url_async

    # SSRF guard — silently drop callbacks to private IPs.
    try:
        await validate_url_async(callback_url)
    except URLGuardError:
        logger.error("Callback URL blocked by SSRF guard: %s", callback_url)
        return

    payload_bytes = payload.model_dump_json().encode()
    signature = sign_payload(payload_bytes, secret)

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
                response = await client.post(
                    callback_url,
                    content=payload_bytes,
                    headers={
                        "Content-Type": "application/json",
                        HMAC_HEADER: signature,
                        "X-Crawler-Job-Id": payload.job_id,
                    },
                )
                if 200 <= response.status_code < 300:
                    logger.info("Callback delivered to %s (job=%s)", callback_url, payload.job_id)
                    return
                logger.warning(
                    "Callback to %s returned HTTP %d (attempt %d/%d)",
                    callback_url,
                    response.status_code,
                    attempt + 1,
                    max_retries,
                )
        except Exception:
            logger.warning(
                "Callback delivery failed for %s (attempt %d/%d)",
                callback_url,
                attempt + 1,
                max_retries,
                exc_info=True,
            )

        if attempt < max_retries - 1:
            await __import__("asyncio").sleep(2**attempt)

    logger.error("Callback exhausted retries for %s (job=%s)", callback_url, payload.job_id)
