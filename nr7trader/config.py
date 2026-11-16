"""Configuration, from the environment / .env in the project folder."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str) -> str:
    # strip inline comments like "1.0   # comment"
    return os.getenv(name, default).split("#")[0].strip()


def _on(name: str, default: str) -> bool:
    return _get(name, default).lower() in ("1", "true", "yes")


TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID", "")

# Kotak Neo Trade API (Neo app > More > Trade API). The consumer key alone gives quotes and the contract lists.
KOTAK_CONSUMER_KEY = _get("KOTAK_CONSUMER_KEY", "")
KOTAK_MOBILE = _get("KOTAK_MOBILE", "")             # registered mobile with country code, e.g. +919876543210
KOTAK_UCC = _get("KOTAK_UCC", "")                   # client code (Neo app > Profile)
# Optional: with both set the app logs in by itself (and again whenever the session lapses). Without them you log
# in on the dashboard with the 6-digit code from your authenticator app (and the MPIN, if it isn't here).
KOTAK_MPIN = _get("KOTAK_MPIN", "")
KOTAK_TOTP_SECRET = _get("KOTAK_TOTP_SECRET", "")   # base32 secret behind the TOTP QR code

# Dashboard
HOST = _get("HOST", "127.0.0.1")
PORT = int(_get("PORT", "8060"))
DASHBOARD_PIN = _get("DASHBOARD_PIN", "")           # other devices log in with it; empty = only this PC

# Sizing (shown lots and the risk budget): RISK_PER_TRADE_PCT of CAPITAL at risk per trade
CAPITAL = float(_get("CAPITAL", "100000"))
RISK_PER_TRADE_PCT = float(_get("RISK_PER_TRADE_PCT", "1.0"))

# Auto-trade (real orders): in-the-money monthly options on these indices, this many lots a trade, the monthly
# expiry at least this many calendar days away
NR7_AUTO_INDICES = [s.strip().upper() for s in _get("NR7_AUTO_INDICES", "NIFTY,BANKNIFTY").split(",") if s.strip()]
NR7_AUTO_LOTS = int(_get("NR7_AUTO_LOTS", "1"))
NR7_OPTION_MIN_DAYS = int(_get("NR7_OPTION_MIN_DAYS", "14"))
# SEBI: no market orders via the API. Orders go in as limits this % past the ask (buy) / bid (sell).
OPTION_PROTECTION_PCT = float(_get("OPTION_PROTECTION_PCT", "5.0"))
OPTIONS_MAX_SPREAD_PCT = float(_get("OPTIONS_MAX_SPREAD_PCT", "5"))   # no buy while the bid-ask spread is wider

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
STATE_DB = DATA_DIR / "nr7.db"
