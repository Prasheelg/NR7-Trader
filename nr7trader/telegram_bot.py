"""Telegram messages via the Bot HTTP API (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)."""
import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


def send_message(text: str, chat_id: str | None = None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN:
        log.info("Telegram not set up; message not sent")
        return False
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {"chat_id": chat_id or config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 429:   # rate limited
                time.sleep(resp.json().get("parameters", {}).get("retry_after", 5))
                continue
            if not resp.ok:
                log.error("Telegram error %s: %s", resp.status_code, resp.text[:300])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("Telegram send failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(2)
    return False
