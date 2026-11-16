# NR7 Trader

Toby Crabel's NR7 breakout on **Nifty 50, Bank Nifty and Sensex**, with a dashboard, Telegram alerts and
auto-trading through **Kotak Neo**. It was moved out of AlgoTrade and runs on its own here, with its own database
(`data/nr7.db`) and dashboard (http://127.0.0.1:8060).

## Setup
```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env      (then fill it in)
```

## Usage
| Command | What it does |
|---|---|
| `run.bat` | Dashboard at http://127.0.0.1:8060, plus the live tracker, the evening scan and the auto-trade bot. Keep it running through market hours while a trade is open |
| `run.bat --login` | Log in to Kotak and show the funds and this PC's IP (places no orders) |
| `run.bat --check` | Auto-trade pre-flight: login, funds, switches, and the exact option the bot would buy for each waiting setup at today's prices (places no orders) |
| `run.bat --backtest` | Backtest on all of Yahoo's daily history (also on the dashboard) |
| `run.bat --history --months 3` | Every setup of the last 1–24 months replayed with the live rules: wins, losses, R and ₹ for one lot |
| `run.bat --test-telegram` | Send a test message |
| `run.bat --import-from PATH` | Copy NR7 setups, trades and settings from an AlgoTrade `data/signals.db` (Trade stays off) |

## Kotak login
`KOTAK_CONSUMER_KEY`, `KOTAK_MOBILE` and `KOTAK_UCC` are always needed (quotes and contract lists need only the
consumer key). Then either:
- **Automatic**: `KOTAK_MPIN` and `KOTAK_TOTP_SECRET` (the base32 text behind the authenticator QR code) in `.env`.
  The app logs in at start and again whenever the session lapses. **Log in now** on the dashboard forces a new login.
- **On the dashboard**: leave `KOTAK_TOTP_SECRET` empty. The **Kotak account** card asks for the 6-digit code your
  authenticator app shows (and the MPIN, unless it is in `.env`). The session lasts the day. Without one, the bot can't
  place or close orders, the dashboard shows a warning, and on a trading day with Auto-trade on Telegram gets one
  reminder.

Once logged in, the card shows the account name and funds. **Log out** closes the session and switches Auto-trade off.
Kotak must have this PC's internet IP registered for Trade API orders (`run.bat --login` prints it) and the F&O
segment active.

Other devices log in to the dashboard with `DASHBOARD_PIN`. Logging in to Kotak and placing or closing orders work
only from this PC, or from a logged-in device over Tailscale. Switching Auto-trade **off** works from anywhere.

## The rules
- **Evening scan**, after 16:00 IST: an index whose daily range (high − low) is the narrowest of the last 7 days gets
  a setup for the **next session only**: buy above that day's high, sell below its low.
- **Next session**: the first level traded is the trade, the other is cancelled. Stop at the NR7 day's other end.
  1R is entry to stop, target 3R from the entry, closed at 15:15 on the 5th session (entry day = 1). If neither level
  trades, the setup expires. One trade per index at a time. An NR7 on the day a trade closed is skipped, and a stopped
  trade is not reversed. Diwali Muhurat and short weekend sessions don't count.
- The tracker follows Yahoo's 1-minute index bars every 30 s in market hours. A missed evening scan is caught up
  before the next session.
- **Skip days** (Budget day and the day before, election results) get no new trade.
- The rules follow `NR7_Breakout_Playbook.html`.

## Auto-trade (real orders)
With **Auto-trade** (top bar) and **Trade (real orders)** both on, each trigger on `NR7_AUTO_INDICES` buys
`NR7_AUTO_LOTS` lot of an in-the-money option: a call above the high, a put below the low. It takes the monthly expiry
at least `NR7_OPTION_MIN_DAYS` days out, with delta nearest 0.7. Triggers come from Kotak's live index quote. An open
that gaps past a level enters at the open. The option is sold when the index reaches the stop or the target, at 15:15
on day 5, or when you press **Sell now**. Orders are limits `OPTION_PROTECTION_PCT` past the ask / bid, NRML,
re-priced after a minute if unfilled. Kotak's positions are checked before a sell, so a lot already closed in the
Kotak app isn't sold again. Nothing rests at Kotak, so the dashboard must stay running. Every step goes to Telegram.

## Tests
`.venv\Scripts\python -m pytest`. The tests never read `.env` or touch the network.
