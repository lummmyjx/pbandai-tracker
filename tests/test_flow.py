"""Offline checks for alerting logic, storage and the dashboard API.

Uses a scripted fake browser, so no network and no Chromium needed.

    .venv\\Scripts\\python.exe tests\\test_flow.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import checker                                            # noqa: E402
import engine                                             # noqa: E402
import notify                                             # noqa: E402
import store                                              # noqa: E402
from config import load_config                            # noqa: E402

FAILURES: list[str] = []
SENT: list[dict] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(f"{label} {detail}")


class FakeFetcher:
    """Returns a scripted sequence of statuses instead of loading a page."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def check(self, url, rules, debug_dir=None):
        status = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if status == checker.ERROR:
            return checker.CheckResult(status=checker.ERROR, error="boom", signal="boom")
        return checker.CheckResult(
            status=status,
            title="Test Gunpla",
            price="S$95.00",
            image="https://img.example/x.jpg",
            signal=f"scripted {status}",
            raw_text_len=500,
        )


def main() -> int:
    cfg = load_config()
    cfg["telegram_bot_token"] = "fake-token"
    cfg["telegram_chat_id"] = "12345"
    cfg["alert_cooldown_minutes"] = 180
    cfg["confirm_reads"] = 1
    cfg["alert_on_blind_streak"] = 2
    rules = checker.load_rules()

    tmp = Path(tempfile.mkdtemp(prefix="pbtracker-test-"))
    store.WATCHLIST_PATH = tmp / "watchlist.json"
    store.STATE_PATH = tmp / "state.json"

    # Capture Telegram sends instead of performing them.
    notify.send = lambda cfg, text, image_url="", link="": SENT.append(
        {"text": text, "image": image_url, "link": link}
    )

    print("\n-- URL handling --")
    entry = store.add_item("p-bandai.com/sg/item/A2866726001/?utm_source=x")
    check("normalises url + derives id", entry["id"] == "A2866726001", entry["url"])

    try:
        store.add_item("https://p-bandai.com/sg/item/A2866726001")
        check("rejects duplicates", False)
    except ValueError:
        check("rejects duplicates", True)

    try:
        store.add_item("https://example.com/item/123")
        check("rejects non-p-bandai links", False)
    except ValueError:
        check("rejects non-p-bandai links", True)

    item = store.load_watchlist()[0]

    print("\n-- alert transitions --")
    fetcher = FakeFetcher([checker.SOLD_OUT])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("sold out sends nothing", len(SENT) == 0, f"sent={len(SENT)}")

    fetcher = FakeFetcher([checker.IN_STOCK])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("sold out -> in stock alerts once", len(SENT) == 1, f"sent={len(SENT)}")
    check("alert carries the buy link",
          SENT and SENT[0]["link"] == item["url"], str(SENT[:1]))
    check("alert carries the image",
          SENT and SENT[0]["image"] == "https://img.example/x.jpg")

    fetcher = FakeFetcher([checker.IN_STOCK, checker.IN_STOCK])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("still in stock does not re-alert", len(SENT) == 1, f"sent={len(SENT)}")

    fetcher = FakeFetcher([checker.SOLD_OUT])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    fetcher = FakeFetcher([checker.IN_STOCK])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("cooldown suppresses rapid re-alert", len(SENT) == 1, f"sent={len(SENT)}")

    print("\n-- going blind is detected, not ignored --")
    SENT.clear()
    store.update_state(item["id"], {"last_alert": None, "blind_streak": 0})
    fetcher = FakeFetcher([checker.ERROR, checker.ERROR, checker.ERROR])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    state = store.load_state()[item["id"]]
    check("a failed check keeps the last known status",
          state.get("last_known_status") == checker.IN_STOCK,
          str(state.get("last_known_status")))
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("warns after consecutive failures", len(SENT) == 1, f"sent={len(SENT)}")
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("warns only once, not every check", len(SENT) == 1, f"sent={len(SENT)}")

    # This is the exact failure that went unnoticed on the PC: readable pages
    # that contain no stock info. It must be treated as blindness, not silence.
    SENT.clear()
    store.update_state(item["id"], {"last_alert": None, "blind_streak": 0,
                                    "last_known_status": checker.SOLD_OUT})
    fetcher = FakeFetcher([checker.UNKNOWN, checker.UNKNOWN])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("one unreadable check stays quiet", len(SENT) == 0, f"sent={len(SENT)}")
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("repeated unreadable checks raise a warning", len(SENT) == 1, f"sent={len(SENT)}")
    state = store.load_state()[item["id"]]
    check("unreadable never overwrites the last known status",
          state.get("last_known_status") == checker.SOLD_OUT,
          str(state.get("last_known_status")))

    SENT.clear()
    store.update_state(item["id"], {"blind_streak": 0})
    fetcher = FakeFetcher([checker.SOLD_OUT])
    engine.check_one(item, fetcher, rules, cfg, "test", log=lambda *_: None)
    check("a good read clears the blind streak",
          store.load_state()[item["id"]].get("blind_streak") == 0)

    print("\n-- status page and history log --")
    import report
    tmp2 = Path(tempfile.mkdtemp(prefix="pbtracker-report-"))
    report.STATUS_PATH = tmp2 / "STATUS.md"
    report.HISTORY_PATH = tmp2 / "history.log"
    line = report.publish({"checked": 1, "alerts": 0, "unreadable": 0, "errors": 0},
                          "cloud backup")
    md = report.STATUS_PATH.read_text(encoding="utf-8")
    check("status page written", report.STATUS_PATH.exists())
    check("status page reports health", "Healthy" in md or "Degraded" in md
          or "Blind" in md, md[:120])
    check("status page lists the item", item["id"] in md or "Test Gunpla" in md)
    check("history line records the source", "cloud backup" in line, line)
    check("history line records per-item status", item["id"] + "=" in line, line)
    report.publish({"checked": 1, "alerts": 0, "unreadable": 0, "errors": 0}, "cloud backup")
    check("history appends rather than overwrites",
          len(report.HISTORY_PATH.read_text(encoding="utf-8").strip().splitlines()) == 2)

    print("\n-- secret handling --")
    # Assembled from pieces on purpose: a literal token-shaped string in this
    # file would trip the pre-commit hook and block a legitimate commit.
    token = "8123456789" + ":" + "AA" + "H7xExampleTokenValue0123456789abc"

    leak = (f"ConnectionError: HTTPSConnectionPool(host='api.telegram.org', "
            f"port=443): Max retries exceeded with url: /bot{token}/sendMessage")
    scrubbed = notify._redact(leak, token)
    check("network errors never expose the token", token not in scrubbed, scrubbed[:90])
    check("redaction leaves the message readable",
          "Max retries exceeded" in scrubbed)

    blind = notify._redact(f"something odd happened near {token}", "")
    check("token shape scrubbed even without knowing the token",
          token not in blind, blind)

    check("normal text passes through untouched",
          notify._redact("A2884010001 is in stock", token) == "A2884010001 is in stock")

    import config as config_mod
    ext = Path(tempfile.mkdtemp(prefix="pbtracker-home-")) / ".pbandai-tracker.json"
    ext.write_text('{"telegram_chat_id": "from-outside-repo"}', encoding="utf-8")
    original = config_mod._external_config_path
    config_mod._external_config_path = lambda: ext
    try:
        loaded = config_mod.load_config()
        check("config outside the repo is picked up",
              loaded["telegram_chat_id"] == "from-outside-repo",
              str(loaded["telegram_chat_id"]))
    finally:
        config_mod._external_config_path = original

    hook = ROOT / "hooks" / "pre-commit"
    check("pre-commit hook ships with the project", hook.exists())

    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for name in ("config.json", ".env", "debug/", ".pbandai-tracker.json"):
        check(f".gitignore covers {name}", name in ignored)

    print("\n-- dashboard api --")
    import app as dashboard
    flask_app = dashboard.build_app(cfg, rules)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    check("dashboard page renders", client.get("/").status_code == 200)

    data = client.get("/api/data").get_json()
    check("api lists the watchlist", len(data["items"]) == 1, str(data["items"]))
    check("api merges live status",
          data["items"][0].get("status") is not None)

    added = client.post("/api/add", json={"url": "https://p-bandai.com/sg/item/A2891018002",
                                          "label": "Hydra"})
    check("api add works", added.status_code == 200 and added.get_json()["ok"])

    bad = client.post("/api/add", json={"url": "not a url"})
    check("api rejects junk urls", bad.status_code == 400)

    removed = client.post("/api/remove", json={"id": "A2891018002"})
    check("api remove works", removed.status_code == 200)
    check("removal shrinks the list",
          len(client.get("/api/data").get_json()["items"]) == 1)

    missing = client.post("/api/remove", json={"id": "nope"})
    check("api remove 404s on unknown id", missing.status_code == 404)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):")
        for line in FAILURES:
            print("  -", line)
        return 1
    print("All flow checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
