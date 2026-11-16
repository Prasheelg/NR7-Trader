"""NR7 Trader: Toby Crabel's NR7 breakout on Nifty 50, Bank Nifty and Sensex, with Kotak Neo auto-trading.

Usage:
  python main.py                        the dashboard (http://127.0.0.1:8060), the tracker and the bot
  python main.py --login                log in to Kotak and show the account (asks for the authenticator code
                                        unless KOTAK_TOTP_SECRET is in .env); no orders
  python main.py --check                auto-trade pre-flight: login, funds, switches, the contracts it would buy
  python main.py --backtest             backtest on daily candles (all of Yahoo's history)
  python main.py --history --months 3   every setup of the last months, replayed with the live rules
  python main.py --test-telegram        send a test message
  python main.py --import-from PATH     copy NR7 setups, trades and settings from an AlgoTrade data/signals.db
"""
import argparse
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from nr7trader import config, state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
log = logging.getLogger("main")


def run_login() -> None:
    from nr7trader import broker
    missing = broker.missing_credentials()
    if missing:
        print(f"Missing in .env: {', '.join(missing)}")
        return
    try:
        if broker.auto_login():
            broker.login(force=True)
        else:
            code = input("6-digit code from your authenticator app: ").strip()
            mpin = config.KOTAK_MPIN or input("Kotak MPIN: ").strip()
            broker.manual_login(code, mpin)
        f = broker.funds()
    except (broker.BrokerError, ValueError) as exc:
        print(f"Kotak login failed: {exc}")
        return
    st = broker.status()
    print(f"Logged in as {st['name'] or config.KOTAK_UCC} ({st['how']} login)")
    print(f"Funds available: Rs {f['available']:,.2f}" if f["available"] is not None else "Funds: unreadable")
    ip = broker.public_ip()
    print(f"This PC's IP as Kotak sees it: {ip or 'unknown'} (register it for Trade API orders if Kotak asks)")
    print("Note: this session belongs to this command only; the dashboard keeps its own.")


def run_check() -> None:
    """Pre-flight for auto-trading: everything it needs, checked, and the contracts it would buy right now for the
    setups it will watch. Read-only: places no orders."""
    from nr7trader import broker, nr7, nr7_auto, nr7_instruments
    problems = []

    def line(ok: bool, text: str, fix: str = "") -> None:
        print(f"  [{'OK' if ok else '!!'}] {text}")
        if not ok:
            problems.append(fix or text)

    print("NR7 auto-trade pre-flight (no orders are placed)\n")
    line(bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID), "Telegram configured",
         "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to get every order step on your phone")
    missing = broker.missing_credentials()
    line(not missing, "Kotak details in .env" + (f" (missing {', '.join(missing)})" if missing else ""),
         f"fill {', '.join(missing)} in .env")
    print(f"  [--] Kotak login: {'automatic (TOTP secret in .env)' if broker.auto_login() else 'on the dashboard each day'}")
    available = None
    if not missing and broker.auto_login():
        try:
            broker.login(force=True)
            available = broker.funds()["available"]
            line(True, f"Kotak login as {broker._session.get('name') or config.KOTAK_UCC}")
            line(available is not None, f"Funds available: Rs {available:,.0f}" if available is not None
                 else "Funds: couldn't read the amount (check the Kotak app)")
        except broker.BrokerError as exc:
            line(False, f"Kotak login failed: {exc}", "fix the Kotak login (run.bat --login)")
        ip = broker.public_ip()
        print(f"  [--] This PC's internet IP: {ip or 'unknown'} (must be registered with Kotak for API orders)")
    s = nr7.settings()
    line(broker.trading_enabled(), "Auto-trade switch is ON", "switch Auto-trade ON on the dashboard")
    line(s["on"] and s["trade"], "Watch and Trade (real orders) are ON", "switch Watch and Trade ON on the dashboard")
    owner = state.get_setting(nr7_auto.OWNER_KEY, None)[0] or {}
    line(nr7_auto._age(owner.get("at")) < nr7_auto.OWNER_STALE_SEC, "Dashboard running with the bot",
         "start the dashboard (run.bat) and keep it running through market hours")
    print(f"  [--] Skip days: {', '.join(s['skip_days']) or 'none'}")
    print(f"  [--] Trades: {', '.join(config.NR7_AUTO_INDICES)}, {config.NR7_AUTO_LOTS} lot, in-the-money options, "
          f"monthly expiry {config.NR7_OPTION_MIN_DAYS}+ days out, delta nearest {nr7_instruments.DELTA_AIM}")

    waiting = [t for t in nr7.setups() if t["status"] == nr7.WAIT and t["idx"] in config.NR7_AUTO_INDICES]
    print(f"\nSetups the bot will watch: {len(waiting) or 'none'}")
    costs = []
    for t in waiting:
        print(f"  {nr7.INDICES[t['idx']][0]} (NR7 {t['nr7_date']}): buy above {t['high']:,.2f} / "
              f"sell below {t['low']:,.2f}")
        if not config.KOTAK_CONSUMER_KEY:
            continue
        try:
            sp = nr7_instruments.spot(t["idx"])
            for opt, label in (("CE", "call if it breaks up"), ("PE", "put if it breaks down")):
                p = nr7_instruments.option_plan(t, opt, sp, datetime.now(nr7_instruments.IST))
                if p is None:
                    line(False, f"    no {opt} contract found", f"no {opt} contract for {t['idx']}")
                    continue
                cost = p["premium"] * p["lot"] * config.NR7_AUTO_LOTS
                costs.append(cost)
                print(f"    {label}: {p['tsym']} (strike {p['strike']:,.0f}, expiry {p['expiry']}, delta "
                      f"{p['delta']}), premium Rs {p['premium']:,.2f} x {p['lot']} = Rs {cost:,.0f}")
        except Exception as exc:
            line(False, f"    contracts / quotes unavailable: {exc}", "Kotak contracts or quotes unavailable")
    if costs and available is not None:
        line(available >= max(costs), f"Funds cover the dearest option (Rs {max(costs):,.0f})",
             f"add funds: one lot can cost up to Rs {max(costs):,.0f}")
    print("\n" + ("READY: the bot will trade the next trigger." if not problems else
                  "NOT READY:\n" + "\n".join(f"  - {p}" for p in problems)))


def run_backtest() -> None:
    from nr7trader import nr7
    res = nr7.backtest()
    print(f"NR7 breakout, daily candles, after costs · {nr7.TARGET_R:g}R target, {nr7.HOLD_DAYS}-day exit")
    for idx, s in res["indices"].items():
        if not s.get("trades"):
            continue
        print(f"\n{nr7.INDICES[idx][0]}: {s['trades']} trades {s['from']} to {s['to']}")
        print(f"  win {s['win_pct']}% · avg {s['avg_r']:+.3f}R · total {s['total_r']:+.1f}R · PF {s['profit_factor']} "
              f"· max DD {s['max_dd_r']}R · worst streak {s['max_losing_streak']}")
    print(f"\nSaved to {nr7.BACKTEST_FILE}")


def run_history(months: int) -> None:
    from nr7trader import nr7
    h = nr7.history(months)
    print(f"NR7 trades with an NR7 day from {h['from']} to {h['to']} ({h['months']} months), daily candles, after costs")
    print(f"Risk per trade Rs {h['risk_budget']:,} ({h['risk_pct']}% of Rs {h['capital']:,.0f})\n")
    for name, s in [(nr7.INDICES[k][0], v) for k, v in h["indices"].items()] + [("All", h["total"])]:
        if not s.get("trades"):
            print(f"{name:<11} {s.get('setups', 0)} setups, no closed trades")
            continue
        print(f"{name:<11} {s['trades']} trades · {s['wins']} won · {s['losses']} lost · {s['total_r']:+.1f}R · "
              f"Rs {s['rs_per_lot']:+,} for one lot")
    print("\nNR7 day   Index       Status    Side   Entry       Exit        Days      R   Rs / 1 lot")
    for t in h["trades"]:
        entry = f"{t['entry']:,.2f}" if t["entry"] else ""
        exit_ = f"{t['exit']:,.2f}" if t.get("exit") else (f"now {t['mark']:,.2f}" if t["status"] == nr7.OPEN
                                                             else (t["note"] or ""))
        r = f"{t['r']:+.2f}" if t.get("r") is not None else ""
        rs = f"{t['rs_per_lot']:+,}" if t.get("rs_per_lot") is not None else ""
        print(f"{t['nr7_date']} {nr7.INDICES[t['idx']][0]:<11} {t['status']:<9} {t['side'] or '':<6} {entry:<11} "
              f"{exit_[:30]:<11} {t.get('days') or '':<5} {r:>6} {rs:>12}")


def import_from(path: str) -> None:
    """Copy NR7 setups, auto-trades, settings and the last scan date from an AlgoTrade signals.db. Rows already
    here are kept. Trade (real orders) and the Auto-trade switch stay OFF: switch them on here once the other
    app no longer trades NR7, or both would place orders."""
    from nr7trader import nr7, nr7_auto
    src = Path(path)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")
    other = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    other.row_factory = sqlite3.Row
    tables = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    counts = {}
    for table, conn in (("nr7_setups", nr7._conn), ("nr7_trades", nr7_auto._conn)):
        if table not in tables:
            continue
        rows = [dict(r) for r in other.execute(f"SELECT * FROM {table}")]
        with conn() as con:
            mine = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            n = 0
            for row in rows:
                row = {k: v for k, v in row.items() if k in mine}
                cur = con.execute(f"INSERT OR IGNORE INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                  list(row.values()))
                n += cur.rowcount
        counts[table] = (n, len(rows))
    for key in (nr7.SETTINGS_KEY, nr7.LAST_SCAN_KEY):
        row = other.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone() if "settings" in tables else None
        if row and state.get_setting(key, None)[0] is None:
            import json
            value = json.loads(row["value"])
            if key == nr7.SETTINGS_KEY:
                value["trade"] = False
            state.set_setting(key, value)
    other.close()
    for table, (n, total) in counts.items():
        print(f"{table}: {n} of {total} rows copied")
    print("Settings copied with Trade (real orders) OFF. Switch Trade and Auto-trade on in NR7 Trader only after "
          "NR7 trading is off in AlgoTrade.")


def main() -> None:
    p = argparse.ArgumentParser(description="NR7 Trader")
    p.add_argument("--login", action="store_true", help="log in to Kotak and show the account (no orders)")
    p.add_argument("--check", action="store_true", help="auto-trade pre-flight (no orders)")
    p.add_argument("--backtest", action="store_true", help="backtest on daily candles")
    p.add_argument("--history", action="store_true", help="setups of the last --months months, replayed")
    p.add_argument("--months", type=int, default=3, help="months for --history (1-24)")
    p.add_argument("--test-telegram", action="store_true", help="send a test message")
    p.add_argument("--import-from", metavar="PATH", help="copy NR7 data from an AlgoTrade data/signals.db")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--port", type=int, default=config.PORT)
    args = p.parse_args()
    if args.login:
        run_login()
    elif args.check:
        run_check()
    elif args.backtest:
        run_backtest()
    elif args.history:
        run_history(args.months)
    elif args.test_telegram:
        from nr7trader import telegram_bot
        print("Sent" if telegram_bot.send_message("📐 NR7 Trader: test message") else "Not sent (see the log)")
    elif args.import_from:
        import_from(args.import_from)
    else:
        from nr7trader import web
        web.serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
