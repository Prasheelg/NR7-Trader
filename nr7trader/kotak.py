"""Kotak Neo market data on the consumer key alone (no login): the SDK client, scrip master downloads, and
parsing quotes. Orders and account reads go through broker.py."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from . import config

log = logging.getLogger(__name__)
BATCH = 49            # quotes per request: Kotak rejects 50
PAUSE_SEC = 1.1       # between quote requests; the API rate-limits back-to-back calls
IST = ZoneInfo("Asia/Kolkata")

_client = None


def enabled() -> bool:
    return bool(config.KOTAK_CONSUMER_KEY)


def _neo():
    """Lazily created SDK client on the consumer key (quotes and scrip masters need no login)."""
    global _client
    if _client is None:
        from neo_api_client import NeoAPI
        logging.getLogger("neo_api_client").setLevel(logging.CRITICAL)   # keep SDK request logs quiet
        _client = NeoAPI(consumer_key=config.KOTAK_CONSUMER_KEY, environment="prod")
    return _client


def _download_csv(url: str) -> str:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def quote_time(q: dict) -> datetime | None:
    """Last update as naive IST. Kotak sends epoch seconds, but a text date for a contract that never traded."""
    try:
        sec = int(q.get("lstup_time") or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(sec, IST).replace(tzinfo=None) if sec > 0 else None


def parse_quote(q: dict) -> dict:
    ohlc = q.get("ohlc") or {}
    prev = ohlc.get("close")
    return {"ltp": float(q["ltp"]), "open": float(ohlc.get("open") or q["ltp"]),
            "high": float(ohlc.get("high") or q["ltp"]), "low": float(ohlc.get("low") or q["ltp"]),
            "volume": float(q.get("last_volume") or 0), "time": quote_time(q),
            "prev_close": float(prev) if prev not in (None, "") and float(prev) > 0 else None}
