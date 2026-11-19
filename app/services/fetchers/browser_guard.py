"""Shared browser egress policy. Network-level egress isolation is still required.

route.fetch(max_redirects=0) is deliberate: route.continue_ lets the browser
follow redirects without running the route handler again. Fulfilling the 3xx
instead makes its next hop a new intercepted request. Service workers must be
blocked on the context; WebSockets are disabled for the interview baseline.
"""

from typing import Any

from app.core.url_guard import MAX_BODY_BYTES, BodyTooLarge, URLGuardError, validate_url_async


async def install_browser_guard(context: Any) -> list[str]:
    """Install before creating/navigating a page; return blocked URLs for diagnostics."""
    blocked: list[str] = []

    async def handle(route: Any) -> None:
        url = route.request.url
        try:
            await validate_url_async(url)
        except URLGuardError:
            blocked.append(url)
            await route.abort("blockedbyclient")
            return
        try:
            response = await route.fetch(max_redirects=0, timeout=30_000)
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > MAX_BODY_BYTES:
                raise BodyTooLarge("Browser resource exceeds body limit")
            body = await response.body()
            if len(body) > MAX_BODY_BYTES:
                raise BodyTooLarge("Browser resource exceeds body limit")
            await route.fulfill(response=response, body=body)
        except BodyTooLarge:
            blocked.append(url)
            await route.abort("blockedbyclient")
        finally:
            # APIRequestContext otherwise retains response buffers until context close.
            if "response" in locals():
                await response.dispose()

    await context.route("**/*", handle)

    async def close_websocket(ws: Any) -> None:
        await ws.close()

    await context.route_web_socket("**/*", close_websocket)
    return blocked


def check_browser_body(body: bytes) -> None:
    """Bound serialized DOM, not total browser RSS; container memory is the hard cap."""
    if len(body) > MAX_BODY_BYTES:
        raise BodyTooLarge("Rendered DOM exceeds body limit")
