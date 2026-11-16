"""Kotak Neo trading session: login, the Auto-trade switch, and logged-in API calls.

Two ways to log in (KOTAK_CONSUMER_KEY, KOTAK_MOBILE and KOTAK_UCC are always needed):
- Automatic: with KOTAK_MPIN and KOTAK_TOTP_SECRET (the base32 secret behind the authenticator QR code) in .env
  the app makes the 6-digit code itself, logs in when needed and again whenever the session lapses.
- From the dashboard: you type the 6-digit code your authenticator app shows (and the MPIN, unless it is in .env).
  The session lasts the day; when it lapses the bot can't trade until you log in again, and says so on Telegram.
A session is kept for the IST day. This module never logs or prints a credential."""
import base64
import hashlib
import hmac
import logging
import struct
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, state

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
REQUIRED = ("KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC")
NEEDS_LOGIN = "Not logged in to Kotak: log in on the dashboard with the code from your authenticator app"

_session = {"client": None, "day": None, "name": None, "at": None, "how": None}
_login_lock = threading.Lock()


class BrokerError(RuntimeError):
    pass


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (SHA-1), as an authenticator app shows it."""
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


def missing_credentials() -> list[str]:
    """What .env lacks for any login at all."""
    return [name for name in REQUIRED if not getattr(config, name, "")]


def auto_login() -> bool:
    """The app can log in by itself (MPIN and TOTP secret in .env)."""
    return bool(config.KOTAK_MPIN and config.KOTAK_TOTP_SECRET)


def _is_empty(res: dict) -> bool:
    """Kotak reports an empty list as an error, in two forms:
    {'error': [{'code': 424, 'message': 'No holdings found ...'}]} and {'stCode': 5203, 'errMsg': 'No Data', ...}."""
    if str(res.get("stCode")) == "5203" or str(res.get("errMsg", "")).lower() == "no data":
        return True
    err = res.get("error") or res.get("Error")
    errs = err if isinstance(err, list) else [err]
    return bool(err) and all(isinstance(e, dict) and str(e.get("code")) == "424"
                             and str(e.get("message", "")).lower().startswith("no ") for e in errs)


def _check(res, step: str) -> dict:
    """The SDK returns error dicts instead of raising; turn them into BrokerError (without credentials)."""
    if not isinstance(res, dict):
        raise BrokerError(f"{step}: unexpected response {str(res)[:200]}")
    if _is_empty(res):
        return {"data": []}
    err = res.get("Error") or res.get("error") or res.get("Error Message")
    if err is None and str(res.get("stat", "")).lower() == "not_ok":
        err = res.get("errMsg") or res.get("emsg") or res.get("desc") or res
    if err is not None:
        raise BrokerError(f"{step} failed: {str(err)[:300]}")
    return res


def _today():
    return datetime.now(IST).date()


def _do_login(code: str, mpin: str, how: str):
    missing = missing_credentials()
    if missing:
        raise BrokerError(f"Missing in .env: {', '.join(missing)}")
    from neo_api_client import NeoAPI
    logging.getLogger("neo_api_client").setLevel(logging.CRITICAL)
    try:
        client = NeoAPI(consumer_key=config.KOTAK_CONSUMER_KEY, environment="prod")
        view = _check(client.totp_login(mobile_number=config.KOTAK_MOBILE, ucc=config.KOTAK_UCC, totp=code),
                      "TOTP login")
        _check(client.totp_validate(mpin=mpin), "MPIN validation")
    except Exception as exc:
        _login_result(str(exc)[:300])
        raise BrokerError(str(exc)) if not isinstance(exc, BrokerError) else exc
    data = view.get("data") or {}
    _session.update(client=client, day=_today(), name=data.get("greetingName"),
                    at=datetime.now().isoformat(timespec="seconds"), how=how)
    _login_result(None)
    log.info("Kotak login OK (%s, %s)", _session["name"] or config.KOTAK_UCC, how)
    return client


def login(force: bool = False):
    """Logged-in NeoAPI client for today. Logs in by itself when it can (auto_login); otherwise raises
    BrokerError(NEEDS_LOGIN) until you log in on the dashboard."""
    with _login_lock:
        if _session["client"] is not None and _session["day"] == _today() and not force:
            return _session["client"]
        if auto_login():
            return _do_login(totp(config.KOTAK_TOTP_SECRET), config.KOTAK_MPIN, "automatic")
        _session.update(client=None)
        _nag()
        raise BrokerError(NEEDS_LOGIN)


def manual_login(code: str, mpin: str | None = None) -> dict:
    """Log in with the 6-digit code from the authenticator app (and the MPIN, unless KOTAK_MPIN is set)."""
    code = str(code or "").strip()
    mpin = str(mpin or "").strip() or config.KOTAK_MPIN
    if not (code.isdigit() and len(code) == 6):
        raise ValueError("The code is the 6 digits your authenticator app shows for Kotak")
    if not (mpin.isdigit() and 4 <= len(mpin) <= 6):
        raise ValueError("Enter your Kotak MPIN (or set KOTAK_MPIN in .env)")
    with _login_lock:
        _do_login(code, mpin, "dashboard")
    return status()


def logout() -> dict:
    with _login_lock:
        client = _session["client"]
        _session.update(client=None, day=None, name=None, at=None, how=None)
    if client is not None:
        try:
            client.logout()
        except Exception as exc:
            log.debug("Kotak logout call failed (%s)", exc)
    log.info("Kotak session closed")
    return status()


LOGIN_KEY = "kotak_login"
NAG_KEY = "kotak_login_nag"


def _login_result(error: str | None) -> None:
    try:
        state.set_setting(LOGIN_KEY, {"ok": error is None, "error": error})
    except Exception:
        log.exception("Could not record the Kotak login result")


def _nag() -> None:
    """Once a trading day: Auto-trade is on but there is no session and the app can't log in by itself."""
    try:
        now = datetime.now(IST)
        if not trading_enabled() or now.weekday() >= 5 or state.get_setting(NAG_KEY, None)[0] == str(now.date()):
            return
        state.set_setting(NAG_KEY, str(now.date()))
        from . import telegram_bot
        telegram_bot.send_message("🔑 <b>NR7 Trader</b>: Auto-trade is on but Kotak isn't logged in. Open the "
                                  "dashboard and log in with the code from your authenticator app.")
    except Exception:
        log.exception("Kotak login reminder failed")


def status() -> dict:
    """The session as the dashboard shows it (no credentials)."""
    last, last_at = state.get_setting(LOGIN_KEY, {"ok": None, "error": None})
    live = _session["client"] is not None and _session["day"] == _today()
    return {"logged_in": live, "name": _session["name"] if live else None, "ucc": config.KOTAK_UCC or None,
            "since": _session["at"] if live else None, "how": _session["how"] if live else None,
            "auto_login": auto_login(), "mpin_saved": bool(config.KOTAK_MPIN),
            "missing": missing_credentials(), "last_error": None if (last or {}).get("ok") else (last or {}).get("error"),
            "last_attempt": last_at}


ORDER_METHODS = ("place_order", "modify_order", "cancel_order")


def _auth_error(msg: str) -> bool:
    """A refusal at the login / permission check, before anything is executed."""
    m = msg.lower()
    return any(k in m for k in ("unauthori", "authenticat", "2fa", "invalid session", "session expired",
                                 "token expired", "invalid token"))


def public_ip() -> str | None:
    """This PC's IP as Kotak sees it (the one to register for API orders)."""
    try:
        data = (login().whatsmyip() or {}).get("data") or []
        return data[0].get("ip") if data else None
    except Exception:
        return None


def call(method: str, **kw):
    """Call a logged-in SDK method. After an authentication refusal: log in again once and retry (automatic login),
    or drop the session so the dashboard asks for a new login. Order calls (place / modify / cancel) are retried
    only after an authentication refusal, which happens before anything executes; after any other failure
    (timeout, server error) the order may have gone through, so a retry could duplicate it."""
    for attempt in (1, 2):
        client = login(force=attempt == 2)
        try:
            return _check(getattr(client, method)(**kw), method)
        except BrokerError as exc:
            auth = _auth_error(str(exc))
            if not auto_login():   # can't log in again by itself, so no retry
                if auth:
                    _session.update(client=None)
                    raise BrokerError(f"{exc}. The Kotak session has lapsed: log in again on the dashboard.") from exc
                raise
            if attempt == 2 or (method in ORDER_METHODS and not auth):
                if method in ORDER_METHODS and auth:
                    ip = public_ip()
                    raise BrokerError(
                        f"{exc}. Kotak accepted the login but refused order access. Usually this means this PC's "
                        f"internet IP{f' ({ip})' if ip else ''} isn't registered for Kotak Trade API orders, or the "
                        "F&O segment isn't active. Register the IP / activate the segment in Kotak, then try again."
                    ) from exc
                raise
            log.warning("Kotak %s failed; logging in again", method)


TRADING_KEY = "trading_enabled"


def trading_status() -> dict:
    """The Auto-trade switch (off until switched on)."""
    st, changed_at = state.get_setting(TRADING_KEY, {"enabled": False})
    return {"enabled": bool(st.get("enabled")), "changed_at": changed_at, "changed_by": st.get("by"),
            "account": st.get("account"), "credentials": not missing_credentials()}


def trading_enabled() -> bool:
    """Checked before every order: a switch-off takes effect on the next order, even mid-loop."""
    return trading_status()["enabled"]


def set_trading(enabled: bool, by: str) -> dict:
    """Switch automatic trading on or off. Switching on first proves the Kotak login works."""
    account_name = None
    if enabled:
        login(force=auto_login())   # raises BrokerError when there is no working session
        account_name = f"{_session['name'] or ''} ({config.KOTAK_UCC})".strip()
    state.set_setting(TRADING_KEY, {"enabled": bool(enabled), "by": by, "account": account_name})
    log.warning("Automatic trading %s by %s", "ENABLED" if enabled else "disabled", by)
    return trading_status()


def _num(row: dict, *names):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def funds() -> dict:
    """Cash available and margin used, from Kotak's limits."""
    lim = call("limits")
    return {"available": _num(lim, "Net"), "margin_used": _num(lim, "MarginUsed", "MarginUsedPrsnt"),
            "collateral": _num(lim, "CollateralValue", "Collateral")}
