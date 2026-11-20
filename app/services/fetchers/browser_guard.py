"""Shared browser egress policy. Network-level egress isolation is still required.

route.fetch(max_redirects=0) is deliberate: route.continue_ lets the browser
follow redirects without running the route handler again. Fulfilling the 3xx
instead makes its next hop a new intercepted request. Service workers must be
blocked on the context; WebSockets are disabled for the interview baseline.
"""

from typing import Any

from app.core.url_guard import MAX_BODY_BYTES, BodyTooLarge, URLGuardError, validate_url_async


async def install_browser_guard(
    context: Any,
    *,
    navigation_headers: dict[str, str] | None = None,
    site_url: str | None = None,
) -> list[str]:
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
            response = await route.fetch(
                max_redirects=0,
                timeout=30_000,
                **_navigation_kwargs(route.request, navigation_headers, site_url),
            )
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


def _navigation_kwargs(
    request: Any, navigation_headers: dict[str, str] | None, site_url: str | None
) -> dict[str, Any]:
    """Request headers reach same-site top-level navigations only, never subresources."""
    if not navigation_headers or not site_url:
        return {}
    from app.services.fetchers.request_options import same_site

    try:
        top_level = request.is_navigation_request() and request.frame.parent_frame is None
        if not top_level or not same_site(site_url, request.url):
            return {}
        merged = dict(request.headers)
    except Exception:
        return {}
    merged.update({name.lower(): value for name, value in navigation_headers.items()})
    return {"headers": merged}


async def prepare_browser_context(context: Any, request_options: Any, url: str) -> list[str]:
    """Install the egress guard and apply per-request cookies and navigation headers."""
    if not request_options:
        return await install_browser_guard(context)
    if request_options.cookies:
        await context.add_cookies(
            [
                {"name": name, "value": value, "url": url}
                for name, value in request_options.cookies.items()
            ]
        )
    return await install_browser_guard(
        context, navigation_headers=dict(request_options.headers), site_url=url
    )
