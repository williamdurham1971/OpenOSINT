"""Local candle cache: data/BTCUSDT_<interval>.csv with columns open_ms,o,h,l,c,v. Resumes where it stopped."""
import csv, time
from pathlib import Path
import mexc

DATA = Path(__file__).parent / "data"
START_2019 = 1546300800000      # 2019-01-01 UTC


def path(interval, symbol=mexc.SYMBOL):
    return DATA / f"{symbol}_{interval}.csv"


def load(interval, symbol=mexc.SYMBOL):
    p = path(interval, symbol)
    if not p.exists():
        return []
    with open(p) as f:
        return [(int(r[0]), *map(float, r[1:6])) for r in csv.reader(f) if len(r) >= 6]


def fetch(interval="60m", start_ms=START_2019, end_ms=None, verbose=True, symbol=mexc.SYMBOL):
    """Download closed candles up to now into the cache; returns the full list."""
    DATA.mkdir(exist_ok=True)
    step = mexc.TF_SECONDS[interval] * 1000
    now_ms = int(time.time() * 1000)
    end_ms = end_ms or now_ms
    have = load(interval, symbol)
    t0 = have[-1][0] + step if have else start_ms
    if t0 <= end_ms:
        def prog(n, t):
            if verbose and (n // mexc.PAGE) % 20 == 0:
                print(f"  {interval}: {n} new candles, at {time.strftime('%Y-%m-%d', time.gmtime(t/1000))}", flush=True)
        rows = [r for r in mexc.klines_range(t0, end_ms, interval, symbol=symbol, progress=prog) if r[0] + step <= now_ms]   # closed only
        if rows:
            with open(path(interval, symbol), "a", newline="") as f:
                csv.writer(f).writerows(rows)
    return load(interval, symbol)


def gaps(rows, interval):
    """Missing bars inside the series (exchange maintenance etc.): list of (after_open_ms, bars_missing)."""
    step = mexc.TF_SECONDS[interval] * 1000
    return [(a[0], (b[0] - a[0]) // step - 1) for a, b in zip(rows, rows[1:]) if b[0] - a[0] != step]
