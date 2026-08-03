from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    print("OK", b.version)
    b.close()
