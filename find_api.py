"""Find the JSON endpoint behind a P-Bandai listing page.

P-Bandai's storefront is a Vue app, so the stock status must arrive from some
background request. If we can find that request, the tracker no longer needs a
headless browser -- it becomes a plain HTTP call, which is ~20x lighter, far
harder to throttle, and cheap enough to host free almost anywhere.

Run it, then send me the api-report.txt it produces:

    .venv\\Scripts\\python.exe find_api.py
    .venv\\Scripts\\python.exe find_api.py https://p-bandai.com/sg/item/A2884010001

Nothing is uploaded anywhere. It writes one local text file.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPORT = BASE_DIR / "api-report.txt"

DEFAULT_URL = "https://p-bandai.com/sg/item/A2884010001"

BODY_CHARS = 2500          # per response, keeps the report readable
MAX_RECORDS = 60

# Keys/words that suggest a response actually carries stock information.
INTERESTING = re.compile(
    r"stock|sold|available|availability|status|price|cart|purchase|order|"
    r"item|goods|product|inventory|quantity|limit",
    re.I,
)


def looks_like_json(response) -> bool:
    ctype = (response.headers or {}).get("content-type", "")
    if "json" in ctype.lower():
        return True
    url = response.url.lower()
    return any(hint in url for hint in ("/api/", "/ajax/", ".json", "graphql"))


def score(url: str, body: str, item_code: str) -> int:
    """Rank candidates so the most likely endpoint appears first."""
    points = 0
    if item_code and item_code.lower() in url.lower():
        points += 50
    if item_code and item_code.lower() in body.lower():
        points += 40
    hits = len(set(m.group(0).lower() for m in INTERESTING.finditer(body[:6000])))
    points += hits * 3
    if re.search(r"sold\s*out|out of stock|in ?stock|add to cart|pre-?order", body, re.I):
        points += 30
    if "/api/" in url.lower():
        points += 10
    if len(body) > 200:
        points += 5
    return points


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    match = re.search(r"/item/([A-Za-z0-9_-]+)", url)
    item_code = match.group(1) if match else ""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is missing. Run setup.bat first.")
        return 1

    print(f"Opening {url}")
    print("Recording background requests... this takes about 30 seconds.\n")

    records = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/127.0.0.0 Safari/537.36"),
            locale="en-SG",
            timezone_id="Asia/Singapore",
        )
        page = context.new_page()

        def on_response(response):
            if len(records) >= MAX_RECORDS or not looks_like_json(response):
                return
            try:
                body = response.text()
            except Exception:
                return
            if not body or not body.strip():
                return
            records.append({
                "url": response.url,
                "status": response.status,
                "method": response.request.method,
                "type": response.request.resource_type,
                "body": body,
            })

        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(6000)
            # Scroll: some sites defer the stock call until the block is visible.
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(4000)
        except Exception as exc:                          # noqa: BLE001
            print(f"Page load problem: {type(exc).__name__}: {exc}")
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    if not records:
        REPORT.write_text(
            f"No JSON-ish responses captured for {url}\n\n"
            "That likely means the page was blocked or served without its data.\n"
            "Try running it again in a minute.\n", encoding="utf-8")
        print("Nothing captured. See api-report.txt")
        return 0

    for record in records:
        record["score"] = score(record["url"], record["body"], item_code)
    records.sort(key=lambda r: r["score"], reverse=True)

    out = [
        "P-Bandai API discovery report",
        f"Page:      {url}",
        f"Item code: {item_code or '(none found)'}",
        f"Captured:  {len(records)} JSON-ish responses, best candidates first",
        "=" * 78,
        "",
    ]

    for index, record in enumerate(records, 1):
        body = record["body"]
        try:                                   # pretty-print so it's readable
            body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
        except Exception:
            pass
        truncated = body[:BODY_CHARS]
        out += [
            f"--- CANDIDATE {index}  (score {record['score']}) " + "-" * 30,
            f"{record['method']} {record['url']}",
            f"HTTP {record['status']}   resource type: {record['type']}",
            f"Body ({len(record['body'])} chars"
            + (", truncated" if len(body) > BODY_CHARS else "") + "):",
            truncated,
            "",
        ]

    REPORT.write_text("\n".join(out), encoding="utf-8")

    print(f"Done. Wrote {REPORT.name} ({REPORT.stat().st_size // 1024} KB)")
    print("\nTop candidates:")
    for record in records[:5]:
        print(f"  score {record['score']:>3}  {record['url'][:110]}")
    print(f"\nSend me {REPORT.name} and I'll tell you if it's usable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
