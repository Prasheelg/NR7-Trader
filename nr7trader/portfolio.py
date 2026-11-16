"""Kotak position rows, normalised (the field names vary between API versions)."""


def _num(row: dict, *names, default=None):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _text(row: dict, *names, default="") -> str:
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v)
    return default


def _rows(res) -> list[dict]:
    data = res.get("data") if isinstance(res, dict) else res
    return [r for r in (data or []) if isinstance(r, dict)] if isinstance(data, list) else []


def norm_position(row: dict) -> dict:
    buy_qty = _num(row, "flBuyQty", default=0.0) + _num(row, "cfBuyQty", default=0.0)
    sell_qty = _num(row, "flSellQty", default=0.0) + _num(row, "cfSellQty", default=0.0)
    if not buy_qty and not sell_qty:
        buy_qty, sell_qty = _num(row, "buyQty", default=0.0), _num(row, "sellQty", default=0.0)
    net = _num(row, "netQty", default=buy_qty - sell_qty)
    return {"tsym": _text(row, "trdSym", "tradingSymbol").strip().upper(), "net_qty": net,
            "segment": _text(row, "exSeg", "exchangeSegment").strip().lower(), "ltp": _num(row, "ltp", "lastPrice")}
