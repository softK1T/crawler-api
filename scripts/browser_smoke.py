"""Check both browser runtimes and JavaScript without external network targets."""

import asyncio
import logging
import platform

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


async def check_browser(name, launch):
    browser = await launch()
    try:
        context = await browser.new_context(
            service_workers="block",
            accept_downloads=False,
        )
        try:
            page = await context.new_page()
            await page.set_content(
                "<html><body><div id='result'></div>"
                "<script>"
                "document.querySelector('#result').textContent = 'JS works';"
                "</script></body></html>",
                timeout=10_000,
            )
            value = await page.locator("#result").inner_text(timeout=10_000)
            if value != "JS works":
                raise RuntimeError(f"{name}: unexpected JavaScript result")
            print(
                f"PASS: {name}; version={browser.version}; JavaScript=OK",
                flush=True,
            )
        finally:
            await asyncio.wait_for(context.close(), timeout=10)
    finally:
        await asyncio.wait_for(browser.close(), timeout=15)


async def main():
    import camoufox

    print(
        f"Platform: {platform.system()} {platform.machine()}; Python {platform.python_version()}",
        flush=True,
    )
    failed = False

    async with async_playwright() as pw:

        async def launch_chromium():
            return await pw.chromium.launch(headless=True, timeout=45_000)

        async def launch_camoufox():
            return await camoufox.AsyncNewBrowser(pw, headless=True, timeout=45_000)

        for name, launch in (
            ("playwright", launch_chromium),
            ("camoufox", launch_camoufox),
        ):
            try:
                await asyncio.wait_for(
                    check_browser(name, launch),
                    timeout=90,
                )
            except Exception:
                failed = True
                logger.exception("FAIL: %s", name)

    return int(failed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
