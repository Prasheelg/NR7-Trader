"""NSE hours (IST)."""
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    tz: str
    session: tuple[time, time]   # regular trading hours, exchange time

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.tz))

    def is_open(self, now: datetime | None = None) -> bool:
        now = now or self.now()
        return now.weekday() < 5 and self.session[0] <= now.time() <= self.session[1]

    def is_after_close(self, now: datetime | None = None) -> bool:
        now = now or self.now()
        return now.weekday() < 5 and now.time() > self.session[1]


MARKETS = {"IN": Market("IN", "India", "Asia/Kolkata", (time(9, 15), time(15, 30)))}


def get(code: str | None = "IN") -> Market:
    code = (code or "IN").upper()
    if code not in MARKETS:
        raise ValueError(f"Unknown market {code}")
    return MARKETS[code]
