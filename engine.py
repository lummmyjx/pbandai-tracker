"""One checking pass: fetch every watched item, update state, decide alerts.

Shared by the local dashboard loop and the GitHub Actions cloud backup, so both
behave identically and share the same de-duplication state.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import checker
import notify
import store
from config import telegram_configured

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "debug"


def _cooldown_active(record: dict, cooldown_minutes: int) -> bool:
    last = store.parse_iso(record.get("last_alert"))
    if not last:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(minutes=cooldown_minutes)


def check_one(item: dict, fetcher, rules, cfg, source: str, log=print) -> dict:
    """Check a single item, persist the outcome, and fire alerts if warranted."""
    state = store.load_state()
    record = dict(state.get(item["id"], {}))
    baseline = record.get("last_known_status")

    debug_dir = DEBUG_DIR if cfg.get("save_debug_on_unknown", True) else None
    result = fetcher.check(item["url"], rules, debug_dir=debug_dir)

    patch = {
        "status": result.status,
        "signal": result.signal,
        "last_checked": store.now_iso(),
        "url": item["url"],
        "checked_by": source,
    }
    if result.title:
        patch["title"] = result.title
    if result.price:
        patch["price"] = result.price
    if result.image:
        patch["image"] = result.image

    alerted = False

    if result.status == checker.ERROR:
        streak = int(record.get("error_streak", 0)) + 1
        patch["error_streak"] = streak
        patch["error"] = result.error
        log(f"  {item['id']}: check failed ({streak}x) - {result.error}")
        threshold = cfg.get("alert_on_error_streak", 5)
        if threshold and streak == threshold and telegram_configured(cfg):
            try:
                notify.send_error_warning(cfg, item, streak, result.error)
            except notify.NotifyError as exc:
                log(f"  ! could not send error warning: {exc}")
    else:
        patch["error_streak"] = 0
        patch["error"] = ""
        patch["last_known_status"] = result.status
        if baseline != result.status:
            patch["last_status_change"] = store.now_iso()

        streak = int(record.get("in_stock_streak", 0))
        streak = streak + 1 if result.status == checker.IN_STOCK else 0
        patch["in_stock_streak"] = streak

        log(f"  {item['id']}: {checker.STATUS_LABEL.get(result.status)} "
            f"({result.signal})")

        needed = max(1, cfg.get("confirm_reads", 1))
        should_alert = (
            result.status == checker.IN_STOCK
            and baseline != checker.IN_STOCK
            and streak >= needed
            and not _cooldown_active(record, cfg.get("alert_cooldown_minutes", 180))
        )

        if should_alert:
            if telegram_configured(cfg):
                try:
                    notify.send_in_stock_alert(cfg, item, result, source=source)
                    patch["last_alert"] = store.now_iso()
                    alerted = True
                    log(f"  >> ALERT SENT for {item['id']}")
                except notify.NotifyError as exc:
                    log(f"  ! ALERT FAILED for {item['id']}: {exc}")
                    patch["notify_error"] = str(exc)
            else:
                log(f"  >> {item['id']} IS IN STOCK but Telegram is not configured")
        elif (cfg.get("alert_on_any_change")
              and baseline
              and baseline != result.status
              and telegram_configured(cfg)):
            try:
                notify.send_status_change(cfg, item, baseline, result, source=source)
            except notify.NotifyError as exc:
                log(f"  ! could not send change note: {exc}")

    patch["alerted"] = alerted
    store.update_state(item["id"], patch)
    return patch


def run_pass(cfg, rules, source: str = "PC", fetcher=None, log=print) -> dict:
    """Check every enabled item once. Reuses a fetcher if one is passed in."""
    items = [i for i in store.load_watchlist() if i.get("enabled", True)]
    if not items:
        log("Watchlist is empty - nothing to check.")
        return {"checked": 0, "alerts": 0}

    log(f"Checking {len(items)} listing(s)...")
    own_fetcher = fetcher is None
    if own_fetcher:
        fetcher = checker.PageFetcher(cfg)
        fetcher.start()

    alerts = unreadable = errors = 0
    try:
        for index, item in enumerate(items):
            patch = check_one(item, fetcher, rules, cfg, source, log=log)
            if patch.get("alerted"):
                alerts += 1
            if patch.get("status") == checker.UNKNOWN:
                unreadable += 1
            elif patch.get("status") == checker.ERROR:
                errors += 1
            if index < len(items) - 1:
                delay = cfg.get("per_item_delay_seconds", 4)
                time.sleep(delay + random.uniform(0, 2.5))
    finally:
        if own_fetcher:
            fetcher.stop()

    return {"checked": len(items), "alerts": alerts,
            "unreadable": unreadable, "errors": errors}
