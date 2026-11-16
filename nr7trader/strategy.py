"""Option maths for picking the NR7 option: Black-Scholes price, implied volatility and Greeks (pure functions).
NSE index options are European, so Black-Scholes applies."""
import math

RISK_FREE = 0.065        # annual risk-free rate used for pricing (roughly the Indian T-bill yield)
MIN_T = 0.5 / 365        # treat the last half-day before expiry as the floor for time value


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_price(opt: str, s: float, k: float, t: float, iv: float, r: float = RISK_FREE) -> float:
    """Black-Scholes value of a European option (t in years)."""
    if t <= 0 or iv <= 0:
        return max(s - k, 0.0) if opt == "CE" else max(k - s, 0.0)
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if opt == "CE":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def implied_vol(opt: str, price: float, s: float, k: float, t: float, r: float = RISK_FREE) -> float | None:
    """Volatility that reproduces the market price (bisection), or None if the price is outside the model range."""
    if not price or price <= 0 or t <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if price < bs_price(opt, s, k, t, lo, r) - 1e-9 or price > bs_price(opt, s, k, t, hi, r):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(opt, s, k, t, mid, r) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def greeks(opt: str, s: float, k: float, t: float, iv: float, r: float = RISK_FREE) -> dict:
    """Per unit: delta, gamma, theta per calendar day, vega per 1 volatility point."""
    t = max(t, MIN_T)
    if not iv or iv <= 0:
        itm = (s > k) if opt == "CE" else (s < k)
        return {"delta": (1.0 if itm else 0.0) * (1 if opt == "CE" else -1), "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sq = math.sqrt(t)
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * sq)
    d2 = d1 - iv * sq
    gamma = _npdf(d1) / (s * iv * sq)
    vega = s * _npdf(d1) * sq / 100
    if opt == "CE":
        delta = _ncdf(d1)
        theta = -s * _npdf(d1) * iv / (2 * sq) - r * k * math.exp(-r * t) * _ncdf(d2)
    else:
        delta = _ncdf(d1) - 1
        theta = -s * _npdf(d1) * iv / (2 * sq) + r * k * math.exp(-r * t) * _ncdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365, "vega": vega}
