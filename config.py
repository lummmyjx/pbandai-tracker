"""Configuration loading for the P-Bandai stock tracker.

Precedence (highest first):
  1. Environment variables            (used by the GitHub Actions cloud backup)
  2. ~/.pbandai-tracker.json          (SAFEST: lives outside the repo entirely,
                                       so it physically cannot be committed)
  3. config.json                      (inside the repo; git-ignored)
  4. Built-in defaults

On Windows, "~" is your user folder, e.g.
    C:\\Users\\YourName\\.pbandai-tracker.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    # --- Telegram ---
    "telegram_bot_token": "",
    "telegram_chat_id": "",

    # --- Checking behaviour ---
    "check_interval_seconds": 90,      # local loop: how long between full passes
    "jitter_seconds": 25,              # random 0..N added, so requests aren't robotic
    "per_item_delay_seconds": 4,       # pause between items in one pass
    "page_timeout_seconds": 45,
    "ready_timeout_seconds": 25,       # how long to wait for the product area to paint
    "settle_seconds": 1.0,             # small pause after it paints
    "max_retries": 2,
    "recycle_after_loads": 25,         # start a fresh browser session this often

    # Automatic backoff: if the site starts refusing to render for us, slow down
    # instead of hammering it harder. Resets as soon as a clean pass comes back.
    "backoff_enabled": True,
    "max_interval_seconds": 900,

    # --- Alerting ---
    "alert_cooldown_minutes": 180,     # don't re-alert the same item within this window
    "confirm_reads": 1,                # consecutive in-stock reads required (1 = instant)
    "alert_on_any_change": False,      # also send a quiet note on any status change
    "alert_on_error_streak": 5,        # warn once after N consecutive failures on an item

    # --- Dashboard ---
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8765,
    "open_browser_on_start": True,

    # --- Misc ---
    "headless": True,
    "save_debug_on_unknown": True,     # dump text+screenshot when status can't be read
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
}

_ENV_MAP = {
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "check_interval_seconds": "CHECK_INTERVAL_SECONDS",
    "alert_cooldown_minutes": "ALERT_COOLDOWN_MINUTES",
    "headless": "HEADLESS",
}

_BOOL_KEYS = {"alert_on_any_change", "headless", "open_browser_on_start",
              "save_debug_on_unknown", "backoff_enabled"}
_INT_KEYS = {"check_interval_seconds", "jitter_seconds", "per_item_delay_seconds",
             "page_timeout_seconds", "max_retries", "alert_cooldown_minutes",
             "confirm_reads", "alert_on_error_streak", "dashboard_port",
             "ready_timeout_seconds", "recycle_after_loads", "max_interval_seconds"}


def _coerce(key, value):
    if key in _BOOL_KEYS:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if key in _INT_KEYS:
        return int(value)
    return value


def _external_config_path() -> Path:
    """A location outside the repo, so a secret kept here can never be committed."""
    return Path.home() / ".pbandai-tracker.json"


def _merge_file(cfg: dict, path: Path, label: str) -> bool:
    if not path.exists():
        return False
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[config] WARNING: could not read {label} ({exc}); ignoring it")
        return False
    for key, value in user.items():
        if key in cfg:
            cfg[key] = _coerce(key, value)
    return True


def load_config(verbose: bool = False) -> dict:
    cfg = dict(DEFAULTS)
    sources = []

    # Lowest precedence first.
    if _merge_file(cfg, BASE_DIR / "config.json", "config.json"):
        sources.append("config.json")

    external = _external_config_path()
    if _merge_file(cfg, external, external.name):
        sources.append(str(external))

    from_env = False
    for key, env_name in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw not in (None, ""):
            cfg[key] = _coerce(key, raw)
            from_env = True
    if from_env:
        sources.append("environment variables")

    if verbose and sources:
        # Never prints the values themselves, only where they came from.
        print(f"[config] loaded from: {', '.join(sources)}")

    return cfg


def telegram_configured(cfg: dict) -> bool:
    return bool(cfg.get("telegram_bot_token")) and bool(cfg.get("telegram_chat_id"))
