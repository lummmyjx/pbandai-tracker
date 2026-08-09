"""Stock detection for P-Bandai listings.

P-Bandai's storefront is a Vue single-page app: the HTML the server sends contains
no product data at all, so a plain HTTP request can never see stock status. We
render the page in a real headless Chromium and read the painted DOM.

The file is deliberately split in two:

  * classify()  -- pure function, no browser. Given the text + buttons scraped
                   from a page, decide the status. Unit-testable offline.
  * PageFetcher -- the Playwright wrapper that produces that input.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

IN_STOCK = "in_stock"
SOLD_OUT = "sold_out"
COMING_SOON = "coming_soon"
ENDED = "ended"
UNKNOWN = "unknown"
ERROR = "error"

BUYABLE = {IN_STOCK}

STATUS_LABEL = {
    IN_STOCK: "IN STOCK",
    SOLD_OUT: "Sold out",
    COMING_SOON: "Coming soon",
    ENDED: "Sales ended",
    UNKNOWN: "Unreadable",
    ERROR: "Check failed",
}

# Cast a wide net for anything that could be a buy control. P-Bandai's markup
# varies between product types, so relying on one class name is fragile. Noise is
# fine: classify() only accepts elements whose *label* matches a buy phrase.
BUTTON_SELECTOR = (
    "button, input[type=submit], input[type=button], [role=button], a[href], "
    "[class*=btn], [class*=Btn], [class*=button], [class*=Button], "
    "[class*=cart], [class*=Cart], [class*=purchase], [class*=Purchase], "
    "[class*=order], [class*=Order], [class*=submit], [class*=Submit], "
    "[class*=buy], [class*=Buy]"
)

# JS runs in the page and returns everything we need in one round trip.
SCRAPE_JS = """
() => {
  const isVisible = (el) => {
    if (!el) return false;
    if (el.getClientRects().length === 0) return false;
    const s = window.getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const isDisabled = (el) => {
    if (el.disabled === true) return true;
    if (el.getAttribute('aria-disabled') === 'true') return true;
    const cls = (el.className && el.className.baseVal !== undefined
                 ? el.className.baseVal : el.className || '') + '';
    return /(^|[\\s_-])(is-)?disabled($|[\\s_-])|soldout|sold-out/i.test(cls);
  };
  const meta = (sel, attr) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute(attr) || '') : '';
  };

  // A wrapper <div class="purchase-area"> inherits the innerText of everything
  // inside it, so matching on containers would read "ADD TO CART" out of a block
  // that actually says sold out. Only real controls count.
  const isControl = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'button' || tag === 'a' || tag === 'input') return true;
    if (el.getAttribute('role') === 'button') return true;
    return el.children.length === 0;
  };

  const buttons = Array.from(document.querySelectorAll(%SELECTOR%))
    .slice(0, 1200)
    .filter(isControl)
    .map((el) => ({
      text: (el.innerText || el.value || el.getAttribute('aria-label') || '')
              .replace(/\\s+/g, ' ').trim().slice(0, 120),
      tag: el.tagName.toLowerCase(),
      cls: ((el.className && el.className.baseVal !== undefined
             ? el.className.baseVal : el.className || '') + '').slice(0, 160),
      disabled: isDisabled(el),
      visible: isVisible(el),
    }))
    .filter((b) => b.text.length > 0 && b.text.length < 80);

  const h1 = document.querySelector('h1');

  return {
    text: (document.body ? document.body.innerText : '').slice(0, 40000),
    html_len: document.documentElement.outerHTML.length,
    title: meta("meta[property='og:title']", 'content') || document.title || '',
    h1: h1 ? (h1.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200) : '',
    image: meta("meta[property='og:image']", 'content'),
    buttons,
  };
}
"""


# Polled in the page until the product area has actually painted. This is the
# difference between "no stock info on this page" and "we looked too early".
READY_JS = """
(src) => {
  if (!document.body) return false;
  try { return new RegExp(src, 'i').test(document.body.innerText); }
  catch (e) { return true; }
}
"""


def load_rules(path: Path | None = None) -> dict:
    path = path or (BASE_DIR / "rules.json")
    rules = json.loads(path.read_text(encoding="utf-8"))
    compiled = {}
    for key in ("buy_text_patterns", "sold_out_patterns", "ended_patterns",
                "coming_soon_patterns", "price_patterns", "buy_exclude_patterns",
                "ready_patterns"):
        compiled[key] = [re.compile(p, re.I) for p in rules.get(key, [])]
    # Single alternation used browser-side to detect "the product area painted".
    compiled["ready_source"] = "|".join(f"(?:{p})" for p in rules.get("ready_patterns", []))
    compiled["raw"] = rules
    return compiled


@dataclass
class CheckResult:
    status: str = UNKNOWN
    title: str = ""
    price: str = ""
    image: str = ""
    signal: str = ""          # the text that drove the decision (shown in dashboard)
    error: str = ""
    raw_text_len: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _first_price(text: str, rules: dict) -> str:
    for pattern in rules["price_patterns"]:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return ""


def _clean_title(raw: str, h1: str) -> str:
    """P-Bandai's og:title is a full SEO string:

        "DIGIMON ... ANTIBODY | DIGIMON | PREMIUM BANDAI Singapore [Official] ..."

    Only the first segment is the product name.
    """
    title = (raw or "").strip()
    if title:
        first = title.split("|")[0].strip()
        if len(first) >= 3:
            return first
        if title:
            return title
    return (h1 or "").strip()


def _matches_any(patterns, value: str):
    for pattern in patterns:
        match = pattern.search(value)
        if match:
            return match.group(0)
    return None


def classify(scrape: dict, rules: dict) -> CheckResult:
    """Decide the status from scraped page data. Pure -- no browser, no network."""
    text = scrape.get("text") or ""
    flat = re.sub(r"\s+", " ", text)
    buttons = scrape.get("buttons") or []

    result = CheckResult(
        title=_clean_title(scrape.get("title"), scrape.get("h1")),
        image=(scrape.get("image") or "").strip(),
        price=_first_price(flat, rules),
        raw_text_len=len(text),
    )

    # An essentially empty render means the SPA never painted -- do NOT call that
    # "sold out", and do not call it "in stock" either. Unknown is the honest answer.
    if len(flat.strip()) < 80:
        result.status = UNKNOWN
        result.signal = "page rendered empty (SPA did not paint)"
        return result

    # 1. An enabled, visible buy control is the strongest possible signal.
    for button in buttons:
        if button.get("disabled") or not button.get("visible"):
            continue
        label = button.get("text", "")
        if _matches_any(rules["buy_exclude_patterns"], label):
            continue
        hit = _matches_any(rules["buy_text_patterns"], label)
        if hit:
            result.status = IN_STOCK
            result.signal = f'active button: "{label}"'
            return result

    # 2. Explicit unavailability wording.
    for status, key in ((SOLD_OUT, "sold_out_patterns"),
                        (ENDED, "ended_patterns"),
                        (COMING_SOON, "coming_soon_patterns")):
        hit = _matches_any(rules[key], flat)
        if hit:
            result.status = status
            result.signal = f'page text: "{hit}"'
            return result

    # 3. A disabled buy button with no wording we recognise -> treat as sold out.
    for button in buttons:
        label = button.get("text", "")
        if button.get("disabled") and _matches_any(rules["buy_text_patterns"], label):
            result.status = SOLD_OUT
            result.signal = f'disabled button: "{label}"'
            return result

    result.status = UNKNOWN
    if not scrape.get("ready", True):
        result.signal = "product area never painted (site slow or throttling us)"
    else:
        result.signal = "no recognised stock wording found"
    return result


class PageFetcher:
    """Reusable headless Chromium. Keep one instance for the whole run."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._context = None
        self.loads_since_recycle = 0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.get("headless", True),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._new_context()

    def _new_context(self):
        self._context = self._browser.new_context(
            user_agent=self.cfg["user_agent"],
            viewport={"width": 1366, "height": 900},
            locale="en-SG",
            timezone_id="Asia/Singapore",
        )
        self._context.set_default_timeout(self.cfg["page_timeout_seconds"] * 1000)
        self.loads_since_recycle = 0

    def recycle_context(self):
        """Drop cookies and session state and start clean.

        Hammering one long-lived session is what gets a storefront's bot
        protection interested in you. Recycling periodically -- and immediately
        after an unreadable page -- keeps each session short and ordinary.
        """
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        self._new_context()

    def stop(self):
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = None

    def scrape(self, url: str, rules: dict) -> dict:
        page = self._context.new_page()
        # Images and fonts are pure weight for us; blocking them roughly halves
        # load time and bandwidth. og:image still comes from the <meta> tag.
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "font", "media")
            else route.continue_(),
        )
        try:
            page.goto(url, wait_until="domcontentloaded")
            self.loads_since_recycle += 1

            # Nudge the page: some storefronts defer rendering the purchase
            # block until it is scrolled towards, and a viewport that never
            # moves is also a giveaway that nobody is really here.
            try:
                page.mouse.wheel(0, 900)
            except Exception:
                pass

            # Wait for real product content rather than a fixed sleep. Under load
            # -- or when the site is throttling us -- a fixed sleep reads a
            # half-painted page and reports a false "unreadable".
            ready = True
            source = rules.get("ready_source") or ""
            if source:
                try:
                    page.wait_for_function(
                        READY_JS, arg=source,
                        timeout=self.cfg.get("ready_timeout_seconds", 25) * 1000,
                    )
                except Exception:
                    ready = False
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

            time.sleep(self.cfg["settle_seconds"])
            js = SCRAPE_JS.replace("%SELECTOR%", json.dumps(BUTTON_SELECTOR))
            data = page.evaluate(js)
            data["ready"] = ready
            return data
        finally:
            try:
                page.close()
            except Exception:
                pass

    def check(self, url: str, rules: dict, debug_dir: Path | None = None) -> CheckResult:
        last_error = ""
        last_unknown = None
        last_scrape = None
        attempts = max(1, self.cfg.get("max_retries", 2))
        recycle_every = self.cfg.get("recycle_after_loads", 25)

        for attempt in range(attempts):
            try:
                if recycle_every and self.loads_since_recycle >= recycle_every:
                    self.recycle_context()

                scrape = self.scrape(url, rules)
                result = classify(scrape, rules)
                if result.status != UNKNOWN:
                    return result

                # Unreadable: most often a throttled or half-served response.
                # Retry once from a clean session before believing it.
                last_unknown, last_scrape = result, scrape
                if attempt < attempts - 1:
                    self.recycle_context()
                    time.sleep(4 + attempt * 4)
            except Exception as exc:                      # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"[:300]
                if attempt < attempts - 1:
                    self.recycle_context()
                    time.sleep(2 + attempt * 3)

        if last_unknown is not None:
            if debug_dir is not None and last_scrape is not None:
                _dump_debug(debug_dir, url, last_scrape)
            return last_unknown

        short = last_error.splitlines()[0][:110] if last_error else "unknown error"
        return CheckResult(status=ERROR, error=last_error, signal=short)


def _dump_debug(debug_dir: Path, url: str, scrape: dict) -> None:
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", url)[-60:]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = debug_dir / f"{stamp}_{slug}.json"
        target.write_text(json.dumps(scrape, indent=2, ensure_ascii=False)[:400000],
                          encoding="utf-8")
    except Exception:
        pass
