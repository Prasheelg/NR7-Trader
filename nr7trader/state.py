"""SQLite store (data/nr7.db): settings as JSON. nr7.py and nr7_auto.py add their own tables to it."""
import json
import sqlite3
from datetime import datetime

from . import config

_wal: set[str] = set()   # databases already switched to WAL by this process


def _conn():
    # The dashboard's threads and a CLI command can share the file: wait for another writer, and use WAL so reads
    # never block a write.
    con = sqlite3.connect(config.STATE_DB, timeout=10)
    con.row_factory = sqlite3.Row
    if str(config.STATE_DB) not in _wal:
        con.execute("PRAGMA journal_mode=WAL")
        _wal.add(str(config.STATE_DB))
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    return con


def get_setting(key: str, default=None):
    """(value, updated_at), or (default, None) when not set."""
    with _conn() as con:
        row = con.execute("SELECT value, updated_at FROM settings WHERE key = ?", (key,)).fetchone()
    return (json.loads(row["value"]), row["updated_at"]) if row else (default, None)


def set_setting(key: str, value) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as con:
        con.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), now))
    return now
