"""Offline checks for the stock-detection engine.

Renders local fixture pages in the same headless Chromium the tracker uses, so it
exercises the real DOM scraper and the real classifier -- no network needed.

Run it any time you edit rules.json:

    .venv\\Scripts\\python.exe tests\\test_detection.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import checker                                            # noqa: E402
from config import load_config                            # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

EXPECTED = {
    # Real P-Bandai SG page shapes
    "preorder_open.html": checker.IN_STOCK,     # "PLACE PRE-ORDER" on an <a>
    "preorder_soldout.html": checker.SOLD_OUT,
    "preorder_not_yet.html": checker.COMING_SOON,
    # Generic shapes
    "in_stock.html": checker.IN_STOCK,
    "preorder.html": checker.IN_STOCK,
    "sold_out.html": checker.SOLD_OUT,
    "hidden_button.html": checker.SOLD_OUT,     # button exists but is display:none
    "disabled_only.html": checker.SOLD_OUT,     # disabled button, no wording
    "coming_soon.html": checker.COMING_SOON,
    "ended.html": checker.ENDED,
    "empty.html": checker.UNKNOWN,              # SPA never painted -> never "sold out"
    "throttled.html": checker.UNKNOWN,          # shell + nav + footer, no product data
}

# The failure mode that matters most: a throttled response is full of text but
# has no product area. It must never be mistaken for a real status.
EXPECTED_SIGNAL = {
    "throttled.html": "never painted",
    "empty.html": "empty",
}

EXPECTED_PRICE = {
    "preorder_open.html": "SG$ 77.00",
    "preorder_soldout.html": "SG$ 45.00",
    "in_stock.html": "S$95.00",
    "preorder.html": "SGD 899.00",
    "sold_out.html": "S$420.00",
}

# og:title is a long SEO string; only the first segment is the product name.
EXPECTED_TITLE = {
    "preorder_open.html": "DIGIMON CARD GAME TAMER'S SELECTION BOX VER. X ANTIBODY",
    "preorder_soldout.html": "ONE PIECE CARD GAME 4TH ANNIVERSARY SET",
}


def main() -> int:
    cfg = load_config()
    cfg["settle_seconds"] = 0.2
    cfg["ready_timeout_seconds"] = 2     # fixtures are local; no need to wait long
    cfg["headless"] = True
    rules = checker.load_rules()

    passed, failed = 0, []

    with checker.PageFetcher(cfg) as fetcher:
        for name, expected in EXPECTED.items():
            path = FIXTURES / name
            result = fetcher.check(path.as_uri(), rules)

            ok = result.status == expected
            price_ok = name not in EXPECTED_PRICE or result.price == EXPECTED_PRICE[name]
            title_ok = name not in EXPECTED_TITLE or result.title == EXPECTED_TITLE[name]
            signal_ok = (name not in EXPECTED_SIGNAL
                         or EXPECTED_SIGNAL[name] in result.signal)

            if ok and price_ok and title_ok and signal_ok:
                passed += 1
                print(f"  PASS  {name:<22} {result.status:<12} {result.signal}")
            else:
                detail = f"got {result.status}, expected {expected}"
                if not price_ok:
                    detail += f" | price {result.price!r} != {EXPECTED_PRICE[name]!r}"
                if not title_ok:
                    detail += f" | title {result.title!r} != {EXPECTED_TITLE[name]!r}"
                if not signal_ok:
                    detail += f" | signal {result.signal!r} lacks {EXPECTED_SIGNAL[name]!r}"
                failed.append(f"{name}: {detail}")
                print(f"  FAIL  {name:<22} {detail}")

    print()
    print(f"{passed}/{len(EXPECTED)} passed")
    if failed:
        print("\nFailures:")
        for line in failed:
            print("  -", line)
        return 1

    print("\nDetection engine looks healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
