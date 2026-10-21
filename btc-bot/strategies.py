"""Indicators and the strategy grid. Long/flat spot only (no shorting, no leverage).

Every strategy maps candle lists to `pos`: pos[t] in {0, 1} is the position to HOLD AFTER the close of bar t, computed only
from bars <= t. The same functions drive the backtest and the live/paper bot, so they cannot drift apart; selftest checks
that truncating the history never changes an already-issued signal (no look-ahead).

The grid below was fixed BEFORE looking at any results: 4 textbook families, a handful of round parameters each,
no tuning. The bar counts are in bars of the chosen timeframe (1h or 4h).
"""
from collections import deque


def sma(x, n):
    out, s, q = [None] * len(x), 0.0, deque()
    for i, v in enumerate(x):
        q.append(v); s += v
        if len(q) > n:
            s -= q.popleft()
        if len(q) == n:
            out[i] = s / n
    return out


def ema(x, n):
    out, k = [None] * len(x), 2.0 / (n + 1)
    if len(x) < n:
        return out
    e = sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def rolling_extreme(x, n, fn):
    """fn(x[i-n:i]) for each i (the window EXCLUDES bar i, so a breakout compares to the past)."""
    out = [None] * len(x)
    for i in range(n, len(x)):
        out[i] = fn(x[i - n:i])
    return out


def ema_trend(o, h, l, c, fast, slow):
    ef, es = ema(c, fast), ema(c, slow)
    return [1 if a is not None and b is not None and a > b else 0 for a, b in zip(ef, es)]


def donchian(o, h, l, c, n):
    """Enter on a close above the highest high of the last n bars, exit on a close below the lowest low of the last n/2."""
    up, dn = rolling_extreme(h, n, max), rolling_extreme(l, max(2, n // 2), min)
    pos, p = [], 0
    for i in range(len(c)):
        if up[i] is not None and c[i] > up[i]:
            p = 1
        elif dn[i] is not None and c[i] < dn[i]:
            p = 0
        pos.append(p)
    return pos


def rsi_dip(o, h, l, c, lo, trend_n, hi=55):
    """Buy an oversold dip only while price is above its long average; exit when RSI recovers or the trend breaks."""
    r, tr = rsi(c, 14), sma(c, trend_n)
    pos, p = [], 0
    for i in range(len(c)):
        if r[i] is not None and tr[i] is not None:
            if p == 0 and r[i] < lo and c[i] > tr[i]:
                p = 1
            elif p == 1 and (r[i] > hi or c[i] < tr[i]):
                p = 0
        pos.append(p)
    return pos


def tsmom(o, h, l, c, lookback):
    """Time-series momentum: hold while the price is above where it was `lookback` bars ago."""
    return [1 if i >= lookback and c[i] > c[i - lookback] else 0 for i in range(len(c))]


def pattern_long(o, h, l, c, mult10=30, hold=72, which="all"):
    """Long on a bullish pattern breakout (patterns.py); exit when a bar touches the swing stop or the measured-move target,
    or after `hold` bars. Causal: entries/exits at bar t use bars <= t only."""
    import patterns as PT
    names = PT.LONG_NAMES if which == "all" else (which,)
    sigs = {}
    for sg in PT.detect(o, h, l, c, mult=mult10 / 10):
        if sg.dir == 1 and sg.name in names:
            sigs.setdefault(sg.t, sg)
    pos, cur, entry_t = [], None, 0
    for t in range(len(c)):
        if cur is not None and t > entry_t and (l[t] <= cur.stop or h[t] >= cur.target or t - entry_t >= hold):
            cur = None
        if cur is None and t in sigs:
            cur, entry_t = sigs[t], t
        pos.append(1 if cur is not None else 0)
    return pos


def pattern_all(o, h, l, c, mult10=30, hold=72):
    return pattern_long(o, h, l, c, mult10, hold, "all")


def pattern_fw(o, h, l, c, mult10=30, hold=72):
    return pattern_long(o, h, l, c, mult10, hold, "falling_wedge")


GRID = (
    [("ema_trend", dict(fast=f, slow=s)) for f, s in ((12, 48), (24, 96), (48, 168), (72, 240), (24, 200), (100, 400))]
    + [("donchian", dict(n=n)) for n in (24, 48, 96, 168, 336, 720)]
    + [("rsi_dip", dict(lo=lo, trend_n=tn)) for lo in (25, 30, 35) for tn in (200, 600)]
    + [("tsmom", dict(lookback=lb)) for lb in (24, 72, 168, 336, 720, 1440)]
)
FUNCS = dict(ema_trend=ema_trend, donchian=donchian, rsi_dip=rsi_dip, tsmom=tsmom, pattern_all=pattern_all, pattern_fw=pattern_fw)
WARMUP = 1500          # bars of history a live bot should load (longest lookback in GRID is 1440)


def name_of(fam, params):
    return fam + "(" + ",".join(f"{k}={v}" for k, v in params.items()) + ")"


def positions(fam, params, o, h, l, c):
    return FUNCS[fam](o, h, l, c, **params)


def resample(rows, factor):
    """Aggregate 1h bars into `factor`-hour bars aligned to the epoch (4h bars start 00,04,08.. UTC).
    Only complete buckets are kept, so a trailing partial bar or a gap never produces a fake candle."""
    step_ms = 3600_000 * factor
    out, cur, n = [], None, 0
    for ts, o, h, l, c, v in rows:
        b = ts - ts % step_ms
        if cur is None or cur[0] != b:
            if cur is not None and n == factor:
                out.append(tuple(cur))
            cur, n = [b, o, h, l, c, v], 1
        else:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v; n += 1
    if cur is not None and n == factor:
        out.append(tuple(cur))
    return out
