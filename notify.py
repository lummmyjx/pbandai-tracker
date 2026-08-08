"""Telegram notifications."""

from __future__ import annotations

import html
import re
import time

import requests

# Telegram bot tokens look like 8123456789:AAH7x...
TOKEN_SHAPE = re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_-]{20,}")

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20


class NotifyError(RuntimeError):
    pass


def _redact(text: str, token: str) -> str:
    """Strip the bot token out of anything we might print or store.

    The token sits in the request URL, so a network exception from requests
    ("...Max retries exceeded with url: /bot8123456789:AAH.../sendMessage")
    would otherwise print it straight into the console, the dashboard log and
    state.json. Belt and braces: also scrub anything shaped like a bot token,
    in case the string came from somewhere we didn't anticipate.
    """
    text = str(text)
    if token:
        text = text.replace(token, "<bot-token>")
        head = token.split(":", 1)[0]
        if head:
            text = text.replace(head, "<bot-id>")
    return TOKEN_SHAPE.sub("<bot-token>", text)


def _call(cfg: dict, method: str, payload: dict, retries: int = 3):
    token = cfg.get("telegram_bot_token", "")
    if not token:
        raise NotifyError("No Telegram bot token configured")

    url = API.format(token=token, method=method)
    last = ""
    for attempt in range(retries):
        try:
            response = requests.post(url, json=payload, timeout=TIMEOUT)
            data = response.json()
            if data.get("ok"):
                return data
            last = _redact(data.get("description", "unknown Telegram error"), token)
            # 429: obey the retry_after Telegram hands back
            if response.status_code == 429:
                wait = int(data.get("parameters", {}).get("retry_after", 3))
                time.sleep(min(wait, 30))
                continue
            if 400 <= response.status_code < 500:
                raise NotifyError(f"Telegram rejected the request: {last}")
        except requests.RequestException as exc:
            last = _redact(f"{type(exc).__name__}: {exc}", token)
        if attempt < retries - 1:
            time.sleep(2 + attempt * 2)
    raise NotifyError(last or "Telegram send failed")


def send(cfg: dict, text: str, image_url: str = "", link: str = "") -> None:
    """Send a message; attach the product image and an Open button when available."""
    chat_id = cfg.get("telegram_chat_id", "")
    if not chat_id:
        raise NotifyError("No Telegram chat id configured")

    markup = None
    if link:
        markup = {"inline_keyboard": [[{"text": "🛒 Open listing", "url": link}]]}

    if image_url:
        try:
            payload = {
                "chat_id": chat_id,
                "photo": image_url,
                "caption": text[:1024],
                "parse_mode": "HTML",
            }
            if markup:
                payload["reply_markup"] = markup
            _call(cfg, "sendPhoto", payload)
            return
        except NotifyError:
            pass  # image hotlink refused or expired -- fall through to plain text

    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if markup:
        payload["reply_markup"] = markup
    _call(cfg, "sendMessage", payload)


def _esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def send_in_stock_alert(cfg: dict, item: dict, result, source: str = "PC") -> None:
    name = item.get("label") or result.title or item["id"]
    lines = [
        "🚨 <b>IN STOCK</b> 🚨",
        "",
        f"<b>{_esc(name)}</b>",
    ]
    if result.price:
        lines.append(f"Price: {_esc(result.price)}")
    lines += [
        f"Item: <code>{_esc(item['id'])}</code>",
        "",
        f'<a href="{_esc(item["url"])}">Buy it now →</a>',
        "",
        f"<i>detected by {_esc(source)} · {_esc(result.signal)}</i>",
    ]
    send(cfg, "\n".join(lines), image_url=result.image, link=item["url"])


def send_status_change(cfg: dict, item: dict, old: str, result, source: str = "PC") -> None:
    from checker import STATUS_LABEL

    name = item.get("label") or result.title or item["id"]
    text = (
        f"ℹ️ <b>{_esc(name)}</b>\n"
        f"{_esc(STATUS_LABEL.get(old, old))} → "
        f"<b>{_esc(STATUS_LABEL.get(result.status, result.status))}</b>\n\n"
        f'<a href="{_esc(item["url"])}">View listing</a>'
    )
    send(cfg, text, link=item["url"])


def send_error_warning(cfg: dict, item: dict, streak: int, error: str) -> None:
    name = item.get("label") or item["id"]
    text = (
        f"⚠️ <b>Tracker problem</b>\n\n"
        f"{_esc(name)} has failed {streak} checks in a row.\n"
        f"<code>{_esc(error[:300])}</code>\n\n"
        f"The listing may have been removed, or P-Bandai changed their page."
    )
    send(cfg, text, link=item.get("url", ""))


def send_test(cfg: dict) -> None:
    send(
        cfg,
        "✅ <b>P-Bandai tracker connected.</b>\n\n"
        "This is a test message. Real alerts will look like this, "
        "with the product image and a buy link.",
    )
