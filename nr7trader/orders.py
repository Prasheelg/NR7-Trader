"""Kotak order-book helpers for the NR7 bot: today's book, an order's state and fill, cancel, and marketable
limit prices. Notices go to Telegram."""
import json
import logging
import math

from . import broker, config, telegram_bot

log = logging.getLogger(__name__)
WATCH_SEC = 5               # the bot checks Kotak this often in market hours


def _notify(text: str) -> None:
    try:
        telegram_bot.send_message(text)
    except Exception:
        log.exception("Telegram order notice failed")


def protect(ref: float, side: str, pct: float, tick: float = 0.05) -> float:
    """Marketable limit for a "market" order: pct % above the reference price for a buy, below it for a sell,
    on the tick grid (rounded away from the reference, so it stays marketable)."""
    n = ref * (1 + pct / 100 if side == "BUY" else 1 - pct / 100) / tick
    n = math.ceil(n - 1e-9) if side == "BUY" else math.floor(n + 1e-9)
    return round(max(n, 1) * tick, 2)


def _book() -> dict[str, dict]:
    """Today's Kotak order book by order number (also saved to data/ for inspection)."""
    res = broker.call("order_report")
    rows = res.get("data") or []
    try:
        (config.DATA_DIR / "kotak_orders_last.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass
    return {str(o.get("nOrdNo")): o for o in rows if o.get("nOrdNo")}


def order_state(o: dict | None) -> str:
    """FILLED / REJECTED / CANCELLED / OPEN, or GONE when the order isn't in today's book (expired)."""
    if o is None:
        return "GONE"
    s = str(o.get("ordSt") or o.get("status") or "").lower()
    if s in ("complete", "completed", "traded", "filled"):
        return "FILLED"
    if "reject" in s:
        return "REJECTED"
    if "cancel" in s or "expire" in s or "lapse" in s:
        return "CANCELLED"
    return "OPEN"


def _filled(o: dict | None) -> tuple[int, float | None]:
    if not o:
        return 0, None
    return int(float(o.get("fldQty") or 0)), float(o.get("avgPrc") or 0) or None


def _reason(o: dict) -> str:
    return str(o.get("rejRsn") or o.get("rejReason") or o.get("ordSt") or "unknown")[:200]


def _cancel(oid: str | None) -> None:
    if oid:
        try:
            broker.call("cancel_order", order_id=oid)
        except broker.BrokerError as exc:
            log.warning("Cancel of order %s failed: %s", oid, exc)
