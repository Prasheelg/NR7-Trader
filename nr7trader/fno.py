"""Index futures and options from Kotak's scrip masters (nse_fo, bse_fo) and their quotes. Read-only."""
import io
import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, kotak

log = logging.getLogger(__name__)
SEGMENT = "nse_fo"
EPOCH_1980 = 315532800      # NSE scrip master expiry dates count seconds from 1980-01-01
# segment -> (instrument types -> FUT / OPT, expiry epoch): NSE sends seconds since 1980, BSE Unix seconds
INDEX_SEGMENTS = {"nse_fo": ({"FUTIDX": "FUT", "OPTIDX": "OPT"}, EPOCH_1980), "bse_fo": ({"IF": "FUT", "IO": "OPT"}, 0)}
_index_master: dict = {}


def _master_text(segment: str) -> str:
    """A segment's scrip master CSV, downloaded once a day and cached on disk across restarts."""
    today = date.today()
    cache = config.DATA_DIR / f"kotak_{segment}_{today}.csv"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    url = kotak._neo().scrip_master(exchange_segment=segment)
    if not (isinstance(url, str) and url.startswith("http")):
        raise RuntimeError(f"Unexpected scrip master response: {str(url)[:200]}")
    text = kotak._download_csv(url)
    cache.write_text(text, encoding="utf-8")
    for old in config.DATA_DIR.glob(f"kotak_{segment}_*.csv"):
        if old != cache:
            old.unlink(missing_ok=True)
    return text


def parse_index_master(csv_text: str, segment: str) -> pd.DataFrame:
    """Index futures (opt "FUT", no strike) and options (CE / PE) from a segment's scrip master."""
    kinds, epoch0 = INDEX_SEGMENTS[segment]
    m = pd.read_csv(io.StringIO(csv_text), dtype=str, low_memory=False)
    m.columns = [c.strip().rstrip(";") for c in m.columns]
    inst = m["pInstType"].str.strip().map(kinds)
    m, inst = m[inst.notna()], inst[inst.notna()]
    exp = pd.to_numeric(m["pExpiryDate"], errors="coerce") + epoch0
    strike = pd.to_numeric(m["dStrikePrice"], errors="coerce") / 100
    df = pd.DataFrame({
        "underlying": m["pSymbolName"].str.strip(),
        "opt": m["pOptionType"].str.strip().where(inst == "OPT", "FUT"),
        "expiry": pd.to_datetime(exp, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date,
        "strike": strike.where(inst == "OPT"),
        "lot": pd.to_numeric(m["lLotSize"], errors="coerce").fillna(0).astype(int),
        "token": m["pSymbol"].str.strip(),
        "tsym": m["pTrdSymbol"].str.strip(),
        "tick": pd.to_numeric(m["dTickSize"], errors="coerce").fillna(5) / 100,
    })
    return df.dropna(subset=["expiry"]).reset_index(drop=True)


def index_contracts(underlying: str, segment: str = SEGMENT) -> pd.DataFrame:
    """An index's live futures and options (e.g. NIFTY on nse_fo, SENSEX on bse_fo), refreshed once a day."""
    today = date.today()
    hit = _index_master.get(segment)
    if not hit or hit[0] != today:
        df = parse_index_master(_master_text(segment), segment)
        hit = _index_master[segment] = (today, df[df["expiry"] >= today].reset_index(drop=True))
    df = hit[1]
    return df[df["underlying"] == underlying].reset_index(drop=True)


def _quotes(tokens: list[str], segment: str = SEGMENT) -> list[dict]:
    """Raw Kotak quotes for instrument tokens (or index names on a cash segment), in batches of 49."""
    out = []
    for n in range(0, len(tokens), kotak.BATCH):
        if n:
            time.sleep(kotak.PAUSE_SEC)
        batch = [{"instrument_token": t, "exchange_segment": segment} for t in tokens[n:n + kotak.BATCH]]
        res = kotak._neo().quotes(instrument_tokens=batch, quote_type="all")
        if not isinstance(res, list):
            raise RuntimeError(f"Kotak quotes failed: {str(res)[:200]}")
        out += res
    return out


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_option_quote(q: dict) -> dict:
    depth = q.get("depth") or {}
    bid = (depth.get("buy") or [{}])[0]
    ask = (depth.get("sell") or [{}])[0]
    return {"ltp": _f(q.get("ltp")), "change": _f(q.get("change")), "pct": _f(q.get("per_change")),
            "volume": _f(q.get("last_volume")), "oi": _f(q.get("open_int")),
            "bid": _f(bid.get("price")) or None, "ask": _f(ask.get("price")) or None,
            "bid_qty": _f(bid.get("quantity")), "ask_qty": _f(ask.get("quantity")),
            "time": t.isoformat(timespec="seconds") if (t := kotak.quote_time(q)) else None}


def days_to_expiry(expiry: date, now: datetime | None = None) -> float:
    """Calendar days (fractional) until 15:30 IST on expiry day."""
    ist = ZoneInfo("Asia/Kolkata")
    now = now or datetime.now(ist)
    end = datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=ist)
    return max((end - now).total_seconds() / 86400, 0.0)
