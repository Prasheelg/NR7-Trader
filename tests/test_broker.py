"""broker: automatic and dashboard Kotak logins, the session, retries, and the web routes around them."""
import sys
import types

import pytest

from conftest import Recorder
from nr7trader import broker, config, state, telegram_bot, web


class FakeNeo:
    """neo_api_client.NeoAPI stand-in: records the login steps; `fail` makes the TOTP step refuse."""
    made: list = []
    fail = None

    def __init__(self, consumer_key=None, environment=None):
        self.steps, self.calls = [], []
        FakeNeo.made.append(self)

    def totp_login(self, mobile_number, ucc, totp):
        self.steps.append(("totp", ucc, totp))
        if FakeNeo.fail:
            return {"error": [{"code": "401", "message": FakeNeo.fail}]}
        return {"data": {"greetingName": "TESTER"}}

    def totp_validate(self, mpin):
        self.steps.append(("mpin", mpin))
        return {"data": {}}

    def limits(self):
        self.calls.append("limits")
        return {"Net": "1234.5", "MarginUsed": "10"}

    def logout(self):
        self.calls.append("logout")


@pytest.fixture
def neo(monkeypatch):
    FakeNeo.made, FakeNeo.fail = [], None
    monkeypatch.setitem(sys.modules, "neo_api_client", types.SimpleNamespace(NeoAPI=FakeNeo))
    for k, v in dict(KOTAK_CONSUMER_KEY="ck", KOTAK_MOBILE="+910000000000", KOTAK_UCC="AB123").items():
        monkeypatch.setattr(config, k, v)
    return FakeNeo


def test_totp_matches_rfc_6238():
    # RFC 6238 test vector (SHA-1): secret "12345678901234567890", T = 59 -> 94287082 (8 digits)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert broker.totp(secret, at=59, digits=8) == "94287082"


def test_without_a_totp_secret_it_waits_for_a_dashboard_login(neo):
    assert not broker.auto_login()
    with pytest.raises(broker.BrokerError, match="log in on the dashboard"):
        broker.login()
    assert neo.made == []


def test_dashboard_login_with_the_authenticator_code(neo, monkeypatch):
    monkeypatch.setattr(config, "KOTAK_MPIN", "1234")
    st = broker.manual_login("123456")
    assert st["logged_in"] and st["name"] == "TESTER" and st["how"] == "dashboard"
    assert neo.made[0].steps == [("totp", "AB123", "123456"), ("mpin", "1234")]
    assert broker.login() is neo.made[0]            # the session is reused for the day
    assert broker.funds()["available"] == 1234.5


@pytest.mark.parametrize("code, mpin", [("12345", "1234"), ("abcdef", "1234"), ("123456", "")])
def test_dashboard_login_checks_its_input(neo, code, mpin):
    with pytest.raises(ValueError):
        broker.manual_login(code, mpin)
    assert neo.made == []


def test_a_refused_login_is_reported_without_a_session(neo):
    neo.fail = "Invalid TOTP"
    with pytest.raises(broker.BrokerError, match="Invalid TOTP"):
        broker.manual_login("123456", "1234")
    st = broker.status()
    assert not st["logged_in"] and "Invalid TOTP" in st["last_error"]


def test_automatic_login_makes_its_own_code(neo, monkeypatch):
    monkeypatch.setattr(config, "KOTAK_MPIN", "1234")
    monkeypatch.setattr(config, "KOTAK_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.setattr(broker, "totp", lambda secret: "654321")
    broker.login()
    assert neo.made[0].steps[0] == ("totp", "AB123", "654321")
    assert broker.status()["how"] == "automatic"


def test_a_dashboard_session_is_not_retried_and_a_lapsed_one_is_dropped(neo, monkeypatch):
    broker.manual_login("123456", "1234")
    client = neo.made[0]
    client.limits = lambda: {"stat": "Not_Ok", "errMsg": "server busy"}
    with pytest.raises(broker.BrokerError, match="server busy"):
        broker.call("limits")
    assert broker.status()["logged_in"]             # a plain failure keeps the session
    client.limits = lambda: {"stat": "Not_Ok", "errMsg": "Invalid session"}
    with pytest.raises(broker.BrokerError, match="log in again"):
        broker.call("limits")
    assert not broker.status()["logged_in"]
    assert len(neo.made) == 1                       # never logged in again by itself


def test_auto_trade_without_a_session_asks_for_a_login_once_a_day(neo, monkeypatch):
    state.set_setting(broker.TRADING_KEY, {"enabled": True})
    sent = Recorder()
    monkeypatch.setattr(telegram_bot, "send_message", sent)
    monkeypatch.setattr(broker, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 11, 17, 9, 0, tzinfo=tz))}))
    for _ in range(3):
        with pytest.raises(broker.BrokerError):
            broker.login()
    assert len(sent.calls) == 1 and "log in" in sent.calls[0]


# ---- web routes ---------------------------------------------------------------------------------------

@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_web_login_logout_and_the_auto_trade_switch(neo, client, monkeypatch):
    monkeypatch.setattr(telegram_bot, "send_message", Recorder())
    st = client.get("/api/status").get_json()
    assert not st["kotak"]["logged_in"] and not st["kotak"]["auto_login"]
    r = client.post("/api/trading", json={"enabled": True})
    assert r.status_code == 502 and "log in" in r.get_json()["error"]
    assert not broker.trading_enabled()

    r = client.post("/api/kotak/login", json={"code": "12", "mpin": "1234"})
    assert r.status_code == 400
    r = client.post("/api/kotak/login", json={"code": "123456", "mpin": "1234"})
    assert r.status_code == 200 and r.get_json()["kotak"]["name"] == "TESTER"
    assert client.post("/api/trading", json={"enabled": True}).get_json()["trading"]["enabled"]
    assert client.get("/api/kotak/funds").get_json()["available"] == 1234.5

    r = client.post("/api/kotak/logout", json={})
    assert not r.get_json()["kotak"]["logged_in"] and not broker.trading_enabled()
    assert "logout" in neo.made[0].calls


def test_other_devices_need_the_pin(client):
    r = client.get("/api/status", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 401
    r = client.post("/api/kotak/login", json={"code": "123456"}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 401
