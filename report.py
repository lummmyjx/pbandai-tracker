"""Human-readable output: a status page, a rolling history log, and the
GitHub Actions run summary.

The point of all three is answering one question at a glance: "is this still
working, or has it quietly gone blind?" A tracker that fails silently is worse
than no tracker, because you stop checking manually.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import checker
import store

BASE_DIR = Path(__file__).resolve().parent
STATUS_PATH = BASE_DIR / "STATUS.md"
HISTORY_PATH = BASE_DIR / "history.log"
HISTORY_MAX_LINES = 2000

# Short codes so a history line stays scannable.
CODE = {
    checker.IN_STOCK: "IN",
    checker.SOLD_OUT: "OUT",
    checker.COMING_SOON: "SOON",
    checker.ENDED: "END",
    checker.UNKNOWN: "??",
    checker.ERROR: "ERR",
}

BADGE = {
    checker.IN_STOCK: "🟢 **IN STOCK**",
    checker.SOLD_OUT: "🔴 Sold out",
    checker.COMING_SOON: "🟡 Coming soon",
    checker.ENDED: "⚫ Sales ended",
    checker.UNKNOWN: "⚪ Unreadable",
    checker.ERROR: "⚠️ Check failed",
}


def _age(iso: str) -> str:
    stamp = store.parse_iso(iso)
    if not stamp:
        return "never"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - stamp).total_seconds()
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def build_status_markdown(source: str = "") -> str:
    items = store.load_watchlist()
    state = store.load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    blind = sum(1 for i in items
                if state.get(i["id"], {}).get("status") in (checker.UNKNOWN, checker.ERROR))
    if not items:
        health = "⚪ Watchlist is empty"
    elif blind == 0:
        health = "🟢 Healthy — every listing read cleanly"
    elif blind < len(items):
        health = f"🟡 Degraded — {blind} of {len(items)} listings unreadable"
    else:
        health = "🔴 Blind — no listing could be read. Site may be throttling us."

    lines = [
        "# P-Bandai tracker status",
        "",
        f"**{health}**",
        "",
        f"Last check: {now}" + (f" (by {source})" if source else ""),
        f"Tracking {len(items)} listing(s).",
        "",
        "| Listing | Status | Price | Last checked | Why |",
        "|---|---|---|---|---|",
    ]

    for item in items:
        record = state.get(item["id"], {})
        status = record.get("status", "never")
        name = item.get("label") or record.get("title") or item["id"]
        if len(name) > 52:
            name = name[:49] + "..."
        lines.append(
            f"| [{name}]({item['url']}) "
            f"| {BADGE.get(status, 'not yet checked')} "
            f"| {record.get('price', '—') or '—'} "
            f"| {_age(record.get('last_checked'))} "
            f"| {(record.get('signal') or '—')[:70]} |"
        )

    lines += [
        "",
        "---",
        "",
        "This page is rewritten on every cloud check. GitHub honours roughly "
        "one scheduled run an hour on a free public repo no matter what the "
        "cron asks for, so gaps of an hour or two are normal. If 'Last check' "
        "is more than about 3 hours old, something has actually stopped — "
        "check the Actions tab.",
        "",
        "See `history.log` for the full run-by-run record.",
    ]
    return "\n".join(lines) + "\n"


def write_status(source: str = "") -> None:
    STATUS_PATH.write_text(build_status_markdown(source), encoding="utf-8")


def append_history(summary: dict, source: str = "", path: Path | None = None) -> str:
    """One line per run. Gaps in the timestamps are how you spot missed runs."""
    state = store.load_state()
    parts = []
    for item in store.load_watchlist():
        status = state.get(item["id"], {}).get("status", "never")
        parts.append(f"{item['id']}={CODE.get(status, '?')}")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = (f"{stamp}  {source:<14} "
            f"checked={summary.get('checked', 0)} "
            f"alerts={summary.get('alerts', 0)} "
            f"unreadable={summary.get('unreadable', 0)} "
            f"failed={summary.get('errors', 0)}  "
            + " ".join(parts))

    path = path or HISTORY_PATH
    existing = []
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []
    existing.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(existing[-HISTORY_MAX_LINES:]) + "\n", encoding="utf-8")
    return line


def write_github_summary(source: str = "") -> None:
    """Render the status table on the Actions run's Summary tab."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(build_status_markdown(source))
    except OSError:
        pass


def publish(summary: dict, source: str = "") -> str:
    write_status(source)
    line = append_history(summary, source)
    write_github_summary(source)
    return line
