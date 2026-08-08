"""Persistence for the watchlist and the last-known state of each listing.

Two files, both plain JSON so you can read or hand-edit them:

  watchlist.json  -- what to watch  (safe to commit / sync to GitHub)
  state.json      -- last seen status per item (also committed, so the cloud
                     backup can de-duplicate alerts across runs)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_PATH = BASE_DIR / "watchlist.json"
STATE_PATH = BASE_DIR / "state.json"

_lock = threading.RLock()

ITEM_CODE_RE = re.compile(r"/item/([A-Za-z0-9_-]+)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalise_url(url: str) -> str:
    """Trim tracking junk and trailing slashes so the same item isn't added twice."""
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is empty")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if "p-bandai.com" not in parsed.netloc.lower():
        raise ValueError("That doesn't look like a p-bandai.com link")

    path = parsed.path.rstrip("/")
    if not path:
        raise ValueError("That link has no item path")
    return f"https://{parsed.netloc.lower()}{path}"


def item_id_from_url(url: str) -> str:
    match = ITEM_CODE_RE.search(url)
    if match:
        return match.group(1)
    return urlparse(url).path.strip("/").replace("/", "_") or "item"


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[store] WARNING: {path.name} unreadable ({exc}); starting fresh")
        return fallback


# --------------------------------------------------------------------------
# Watchlist
# --------------------------------------------------------------------------

def load_watchlist() -> list:
    with _lock:
        data = _read_json(WATCHLIST_PATH, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else data
        cleaned = []
        for entry in items:
            if isinstance(entry, str):
                entry = {"url": entry}
            url = entry.get("url")
            if not url:
                continue
            cleaned.append({
                "id": entry.get("id") or item_id_from_url(url),
                "url": url,
                "label": entry.get("label", ""),
                "enabled": entry.get("enabled", True),
                "added": entry.get("added") or now_iso(),
            })
        return cleaned


def save_watchlist(items: list) -> None:
    with _lock:
        _atomic_write(WATCHLIST_PATH, {"items": items})


def add_item(url: str, label: str = "") -> dict:
    url = normalise_url(url)
    item_id = item_id_from_url(url)
    with _lock:
        items = load_watchlist()
        for existing in items:
            if existing["id"] == item_id:
                raise ValueError(f"{item_id} is already on the watchlist")
        entry = {"id": item_id, "url": url, "label": label,
                 "enabled": True, "added": now_iso()}
        items.append(entry)
        save_watchlist(items)
        return entry


def remove_item(item_id: str) -> bool:
    with _lock:
        items = load_watchlist()
        remaining = [i for i in items if i["id"] != item_id]
        if len(remaining) == len(items):
            return False
        save_watchlist(remaining)
        state = load_state()
        state.pop(item_id, None)
        save_state(state)
        return True


def set_enabled(item_id: str, enabled: bool) -> bool:
    with _lock:
        items = load_watchlist()
        found = False
        for entry in items:
            if entry["id"] == item_id:
                entry["enabled"] = bool(enabled)
                found = True
        if found:
            save_watchlist(items)
        return found


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state() -> dict:
    with _lock:
        data = _read_json(STATE_PATH, {})
        return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    with _lock:
        _atomic_write(STATE_PATH, state)


def update_state(item_id: str, patch: dict) -> dict:
    with _lock:
        state = load_state()
        record = state.get(item_id, {})
        record.update(patch)
        state[item_id] = record
        save_state(state)
        return record
