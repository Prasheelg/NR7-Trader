"""Dashboard access (access.py): PIN login for other devices; trading only from this PC, over Tailscale,
or through Cloudflare Access with a valid token."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nr7trader import access, config, telegram_bot, web

LAN, TAILNET = "192.168.1.20", "100.101.7.3"
TS = {"Tailscale-User-Login": "me@example.com", "X-Forwarded-For": TAILNET}   # as `tailscale serve` proxies
TEAM, AUD, ME = "myteam.cloudflareaccess.com", "aud-tag-123", "me@example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PIN", "246810")
    monkeypatch.setattr(access, "_fails", {"n": 0, "until": 0.0})
    monkeypatch.setattr(telegram_bot, "send_message", lambda *a, **k: True)
    return web.app.test_client()


@pytest.fixture
def cf(monkeypatch):
    """Cloudflare Access configured; the team's public key is KEY's (no network)."""
    monkeypatch.setattr(config, "CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(config, "CF_ACCESS_AUD", [AUD])
    monkeypatch.setattr(config, "CF_ACCESS_EMAILS", [ME])

    class Jwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": KEY.public_key()})()
    monkeypatch.setattr(access, "_jwk_client", lambda: Jwks())


def get(c, path, addr=LAN, headers=None):
    return c.get(path, environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


def post(c, path, body, addr=LAN, headers=None):
    return c.post(path, json=body, environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


def login(c, addr=LAN, headers=None):
    return post(c, "/api/login", {"pin": "246810"}, addr, headers)


def cf_token(email=ME, aud=AUD, iss=f"https://{TEAM}", exp_in=600, key=KEY):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": iss, "iat": now, "exp": now + exp_in},
                      key, algorithm="RS256", headers={"kid": "k1"})


def cf_headers(token=None):
    """As cloudflared proxies a request that passed Cloudflare Access."""
    return {"Cf-Access-Jwt-Assertion": token or cf_token(), "Cf-Connecting-Ip": "203.0.113.9",
            "Cf-Ray": "abc-BOM", "X-Forwarded-For": "203.0.113.9"}


def can_trade(c, addr, headers):
    return get(c, "/api/access", addr, headers).get_json()["can_trade"]


def test_this_pc_needs_no_login_and_can_trade(c):
    assert can_trade(c, "127.0.0.1", {}) is True


def test_proxied_requests_are_not_local(c):
    for h in (TS, {"X-Forwarded-For": "1.2.3.4"}, {"Cf-Ray": "abc"}, {"Cf-Connecting-Ip": "203.0.113.9"},
              {"Cf-Access-Jwt-Assertion": "x"}):
        assert get(c, "/api/trading", addr="127.0.0.1", headers=h).status_code == 401, h


def test_logged_in_lan_device_can_only_look(c):
    login(c)
    assert can_trade(c, LAN, {}) is False
    r = post(c, "/api/nr7/scan", {})
    assert r.status_code == 403 and "Cloudflare Access" in r.get_json()["error"]


def test_logged_in_tailscale_device_can_trade(c):
    login(c, "127.0.0.1", TS)
    assert can_trade(c, "127.0.0.1", TS) is True


def test_logged_in_cloudflare_device_can_trade(c, cf):
    h = cf_headers()
    login(c, "127.0.0.1", h)
    st = get(c, "/api/access", "127.0.0.1", h).get_json()
    assert st["can_trade"] is True and st["device"] == f"{ME} via Cloudflare"


def test_cloudflare_without_pin_login_is_refused(c, cf):
    assert get(c, "/api/trading", "127.0.0.1", cf_headers()).status_code == 401


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    cf_token(key=OTHER_KEY),
    cf_token(aud="another-app"),
    cf_token(iss="https://evil.cloudflareaccess.com"),
    cf_token(exp_in=-60),
    cf_token(email="stranger@example.com"),
], ids=["garbage", "wrong-key", "wrong-aud", "wrong-issuer", "expired", "email-not-allowed"])
def test_bad_cloudflare_tokens_can_only_look(c, cf, token):
    h = cf_headers(token)
    login(c, "127.0.0.1", h)
    assert can_trade(c, "127.0.0.1", h) is False
    assert post(c, "/api/nr7/scan", {}, "127.0.0.1", h).status_code == 403


def test_cloudflare_token_replayed_from_elsewhere_doesnt_count(c, cf):
    h = cf_headers()
    login(c, LAN, h)
    assert can_trade(c, LAN, h) is False


def test_cloudflare_not_configured_means_look_only(c, cf, monkeypatch):
    monkeypatch.setattr(config, "CF_ACCESS_EMAILS", [])
    h = cf_headers()
    login(c, "127.0.0.1", h)
    assert can_trade(c, "127.0.0.1", h) is False
