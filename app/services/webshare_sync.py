import logging
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

WEBSHARE_LIST_URL = "https://proxy.webshare.io/api/v2/proxy/list/"


def fetch_webshare_proxies(api_key: str, page_size: int = 100) -> List[str]:
    """
    Fetch all proxies from Webshare API v2.
    Returns list of strings in format: host:port:user:pass:COUNTRY
    """
    lines: List[str] = []
    page = 1
    headers = {"Authorization": f"Token {api_key}"}

    with httpx.Client(timeout=30) as client:
        while True:
            params = {"mode": "direct", "page": page, "page_size": page_size}
            resp = client.get(WEBSHARE_LIST_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            for proxy in data.get("results", []):
                host = proxy.get("proxy_address", "")
                port = proxy.get("port", "")
                username = proxy.get("username", "")
                password = proxy.get("password", "")
                country = proxy.get("country_code", "US").upper()

                if host and port and username and password:
                    lines.append(f"{host}:{port}:{username}:{password}:{country}")

            logger.info("Webshare sync: fetched page %d (%d proxies so far)", page, len(lines))

            # Check if there are more pages
            if not data.get("next"):
                break
            page += 1

    return lines


def sync_webshare_to_file(api_key: str, output_path: str) -> int:
    """
    Fetch proxies from Webshare and write to file.
    Returns number of proxies written.
    """
    logger.info("Starting Webshare proxy sync -> %s", output_path)
    lines = fetch_webshare_proxies(api_key)

    if not lines:
        logger.warning("Webshare returned 0 proxies, skipping file write")
        return 0

    # Ensure parent directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    logger.info("Webshare sync complete: %d proxies written to %s", len(lines), output_path)
    return len(lines)
