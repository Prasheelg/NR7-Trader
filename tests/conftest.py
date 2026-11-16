"""Test harness: .env is never read, the network is blocked, every test gets its own database."""
import os

import dotenv

dotenv.load_dotenv = lambda *a, **k: False   # .env (real credentials) is never read
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN",
           "KOTAK_TOTP_SECRET", "DASHBOARD_PIN"):
    os.environ[_k] = ""
for _k in ("CAPITAL", "RISK_PER_TRADE_PCT", "NR7_AUTO_INDICES", "NR7_AUTO_LOTS", "NR7_OPTION_MIN_DAYS"):
    os.environ.pop(_k, None)

import socket  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402
import yfinance  # noqa: E402

from nr7trader import broker, config, telegram_bot  # noqa: E402

assert config.TELEGRAM_BOT_TOKEN == "" and config.KOTAK_CONSUMER_KEY == ""
_TMP = Path(tempfile.mkdtemp(prefix="nr7trader-tests-"))
config.STATE_DB, config.DATA_DIR = _TMP / "nr7.db", _TMP


def _no_network(*a, **k):
    raise RuntimeError("network blocked in tests")


def _unexpected_telegram(*a, **k):
    pytest.fail(f"unexpected Telegram send: {a!r} {k!r}")


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(yfinance, "download", _no_network)
    monkeypatch.setattr(requests.Session, "request", _no_network)
    monkeypatch.setattr(telegram_bot, "send_message", _unexpected_telegram)
    monkeypatch.setattr(config, "STATE_DB", tmp_path / "nr7.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for k, v in dict(CAPITAL=100000.0, RISK_PER_TRADE_PCT=1.0, TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="",
                     KOTAK_CONSUMER_KEY="", KOTAK_MOBILE="", KOTAK_UCC="", KOTAK_MPIN="", KOTAK_TOTP_SECRET="",
                     DASHBOARD_PIN="").items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(broker, "_session", {"client": None, "day": None, "name": None, "at": None, "how": None})
    yield


class Recorder:
    """Stands in for a function; records each call's first argument."""

    def __init__(self, result=True):
        self.calls, self.result = [], result

    def __call__(self, *a, **k):
        self.calls.append(a[0] if a else k)
        return self.result


class FakeKotak:
    """Order book stand-in behind broker.call: place / cancel / order_report."""

    def __init__(self):
        self.book, self.placed, self.cancelled, self.n = {}, [], [], 100
        self.fail_place = []      # BrokerError messages for the next place_order calls

    def __call__(self, method, **kw):
        if method == "order_report":
            return {"data": list(self.book.values())}
        if method == "place_order":
            if self.fail_place:
                raise broker.BrokerError(f"place_order failed: {self.fail_place.pop(0)}")
            self.n += 1
            oid = str(self.n)
            self.book[oid] = {"nOrdNo": oid, "ordSt": "open", "trdSym": kw["trading_symbol"],
                              "trnsTp": kw["transaction_type"], "qty": kw["quantity"], "fldQty": "0", "avgPrc": "0",
                              "rejRsn": ""}
            self.placed.append({**kw, "oid": oid})
            return {"nOrdNo": oid, "stat": "Ok", "stCode": 200}
        if method == "cancel_order":
            self.cancelled.append(kw["order_id"])
            self.book[kw["order_id"]]["ordSt"] = "cancelled"
            return {"stat": "Ok"}
        raise AssertionError(f"unexpected call {method}")

    def fill(self, oid, qty, price, status="complete"):
        self.book[oid].update(ordSt=status, fldQty=str(qty), avgPrc=str(price))
