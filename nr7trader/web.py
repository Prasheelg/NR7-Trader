"""The NR7 Trader dashboard (Flask): http://127.0.0.1:8060 by default.

Background threads: the monitor (nr7.check / nr7.scan: the live tracker and the evening scan) and the auto-trade bot
(nr7_auto.watch). Both need this process running through market hours."""
import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory

from . import access, broker, config, markets, nr7, nr7_auto, telegram_bot

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MONITOR_SEC = 15

app = Flask(__name__)
access.setup(app)
_monitor_state = {"at": None, "error": None}


@app.errorhandler(ValueError)
def _bad_request(exc):
    return jsonify({"error": str(exc)}), 400


def _refused():
    return jsonify({"error": access.TRADE_REFUSED}), 403


# ---- pages and dashboard access -----------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/login")
def login_page():
    if access.is_local() or access.logged_in():
        return redirect("/")
    return send_from_directory(STATIC, "login.html")


@app.post("/api/login")
def api_login():
    ok, msg = access.login(str((request.get_json(silent=True) or {}).get("pin", "")))
    return (jsonify({"ok": True}), 200) if ok else (jsonify({"error": msg}), 401)


@app.post("/api/logout")
def api_logout():
    access.logout()
    return jsonify({"ok": True})


@app.get("/api/access")
def api_access():
    return jsonify(access.status())


# ---- Kotak session and the Auto-trade switch -----------------------------------------------------------

def _status() -> dict:
    m = markets.get("IN")
    return {"kotak": broker.status(), "trading": broker.trading_status(), "access": access.status(),
            "market_open": m.is_open(), "now": m.now().strftime("%Y-%m-%d %H:%M:%S"),
            "monitor": _monitor_state, "telegram": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)}


@app.get("/api/status")
def api_status():
    return jsonify(_status())


@app.post("/api/kotak/login")
def api_kotak_login():
    """{"code": 6 digits from the authenticator app, "mpin"?}; or {} to log in automatically (TOTP secret in .env)."""
    if not access.can_trade():
        return _refused()
    body = request.get_json(silent=True) or {}
    try:
        if body.get("code"):
            broker.manual_login(body.get("code"), body.get("mpin"))
        else:
            broker.login(force=True)
    except broker.BrokerError as exc:
        return jsonify({"error": f"Kotak login failed: {exc}"}), 502
    log.warning("Kotak login from %s", access.who())
    return jsonify(_status())


@app.post("/api/kotak/logout")
def api_kotak_logout():
    """Close the Kotak session. Auto-trade is switched off with it (it couldn't place or close orders anyway)."""
    if not access.can_trade():
        return _refused()
    if broker.trading_enabled():
        broker.set_trading(False, by=f"Kotak logout ({access.who()})")
        _tell("🔴 <b>Automatic trading OFF</b> (Kotak logged out)")
    broker.logout()
    return jsonify(_status())


@app.get("/api/kotak/funds")
def api_kotak_funds():
    try:
        return jsonify(broker.funds())
    except broker.BrokerError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/trading")
def api_trading():
    return jsonify(broker.trading_status())


@app.post("/api/trading")
def api_trading_set():
    """Switch automatic trading. ON needs this PC or a logged-in device over Tailscale (access.py); OFF works from
    anywhere, so trading can always be stopped."""
    body = request.get_json(silent=True) or {}
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled: true or false")
    if enabled and not access.can_trade():
        return _refused()
    try:
        st = broker.set_trading(enabled, by=f"dashboard ({access.who()})")
    except broker.BrokerError as exc:
        return jsonify({"error": f"Kotak login needed, trading stays off: {exc}"}), 502
    _tell(f"🟢 <b>Automatic trading ON</b> · {st['account']}" if enabled else "🔴 <b>Automatic trading OFF</b>")
    return jsonify(_status())


def _tell(text: str) -> None:
    try:
        telegram_bot.send_message(f"📐 NR7 · {text}")
    except Exception:
        log.exception("Telegram notice failed")


# ---- NR7 -------------------------------------------------------------------------------------------------

def _nr7_view() -> dict:
    return {**nr7.view(), "auto": nr7_auto.view()}


@app.get("/api/nr7")
def api_nr7():
    """Settings, setups and trades, the last backtest, the auto-trade's orders."""
    return jsonify(_nr7_view())


@app.post("/api/nr7/settings")
def api_nr7_settings():
    """{on, send, trade, skip_days}: any of them."""
    if not access.can_trade():
        return _refused()
    body = request.get_json(silent=True) or {}
    if body.get("trade") is True and broker.missing_credentials():
        return jsonify({"error": "Kotak details are missing in .env (" + ", ".join(broker.missing_credentials())
                        + "), so no orders can be placed"}), 400
    s = nr7.save_settings(body)
    log.warning("NR7 settings changed from %s: %s", access.who(), s)
    return jsonify(_nr7_view())


@app.post("/api/nr7/scan")
def api_nr7_scan():
    """Look for an NR7 on the latest completed session now, instead of waiting for the evening scan."""
    if not access.can_trade():
        return _refused()
    made = nr7.scan(force=True)
    return jsonify({**_nr7_view(), "made": len(made)})


@app.post("/api/nr7/auto/close")
def api_nr7_auto_close():
    """{"key"}: close an auto-trade now (cancel a waiting one, sell an open one)."""
    if not access.can_trade():
        return _refused()
    key = str((request.get_json(silent=True) or {}).get("key", ""))
    try:
        nr7_auto.close_trade(key)
    except broker.BrokerError as exc:
        return jsonify({"error": str(exc)}), 502
    log.warning("NR7 trade %s closed from %s", key, access.who())
    return jsonify(_nr7_view())


@app.get("/api/nr7/history")
def api_nr7_history():
    """?months=3: every NR7 setup of the last months, replayed on daily candles."""
    try:
        months = int(request.args.get("months", 3))
    except ValueError:
        raise ValueError("months: a number from 1 to 24")
    try:
        return jsonify(nr7.history(months))
    except Exception as exc:
        log.exception("NR7 history failed")
        return jsonify({"error": f"NR7 history unavailable: {exc}"}), 502


@app.post("/api/nr7/backtest")
def api_nr7_backtest():
    """Run the daily-candle backtest on all of Yahoo's history (a few seconds) and return the view."""
    if not access.can_trade():
        return _refused()
    try:
        nr7.backtest()
    except Exception as exc:
        log.exception("NR7 backtest failed")
        return jsonify({"error": f"Backtest failed: {exc}"}), 502
    return jsonify(_nr7_view())


# ---- background work -------------------------------------------------------------------------------------

def _monitor() -> None:
    """The live tracker and the evening scan (check first: it expires the day's setups before the scan)."""
    while True:
        for job in (nr7.check, nr7.scan):
            try:
                job()
                _monitor_state.update(at=time.strftime("%Y-%m-%d %H:%M:%S"), error=None)
            except Exception as exc:
                log.exception("NR7 %s failed", job.__name__)
                _monitor_state["error"] = f"{job.__name__}: {exc}"[:300]
        time.sleep(MONITOR_SEC)


def _first_login() -> None:
    """With the TOTP secret in .env, log in at start so the dashboard shows the account at once."""
    if broker.auto_login() and not broker.missing_credentials():
        try:
            broker.login()
        except broker.BrokerError as exc:
            log.warning("Kotak login at start failed: %s", exc)


def serve(host: str = config.HOST, port: int = config.PORT) -> None:
    threading.Thread(target=_first_login, name="kotak-login", daemon=True).start()
    threading.Thread(target=_monitor, name="nr7-monitor", daemon=True).start()
    threading.Thread(target=nr7_auto.watch, args=(threading.Event(),), name="nr7-auto", daemon=True).start()
    log.info("NR7 Trader running at http://%s:%d", host, port)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # the page polls; keep only warnings
    app.run(host=host, port=port, debug=False, use_reloader=False)
