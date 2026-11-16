"""Index candles from Yahoo Finance (daily for the scan and the backtest, 1-minute for the live tracker)."""
import logging

import pandas as pd
import yfinance as yf

from . import markets

log = logging.getLogger(__name__)


def fetch_ohlcv(tickers: list[str], timeframe: str = "1d", period: str = "1y",
                market: str = "IN") -> dict[str, pd.DataFrame]:
    """{Yahoo ticker: DataFrame[Open, High, Low, Close, Volume]} with naive exchange-time timestamps."""
    tz = markets.get(market).tz
    try:
        raw = yf.download(tickers, period=period, interval=timeframe, group_by="ticker", auto_adjust=True,
                          progress=False, threads=True)
    except Exception as exc:
        log.error("Yahoo download failed for %s: %s", tickers, exc)
        return {}
    flat = not isinstance(raw.columns, pd.MultiIndex)
    if flat and len(tickers) > 1:
        log.error("Yahoo returned a flat frame for %s; skipped", tickers)
        return {}
    out = {}
    for t in tickers:
        try:
            df = (raw if flat else raw[t])[["Open", "High", "Low", "Close", "Volume"]].dropna(
                subset=["Open", "High", "Low", "Close"])
        except KeyError:
            continue
        if df.index.tz is not None:
            df.index = df.index.tz_convert(tz).tz_localize(None)
        if len(df):
            out[t] = df
    log.debug("Yahoo %s %s: %d/%d", timeframe, period, len(out), len(tickers))
    return out
