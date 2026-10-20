"""Backtest engine, statistics, and a data-snooping check.

Timing: pos[t] is decided at the close of bar t and filled at that close; the position earns the NEXT bar's return
(`delay` adds extra bars of latency). Costs are charged on every position change: taker fee + slippage per side.
Spot, long/flat, 100% of the allocated capital when long.
"""
import math, random

FEE, SLIP = 0.0005, 0.0002        # MEXC BTCUSDT taker 0.05% (exchangeInfo); 0.02% assumed slippage per side


def returns(close):
    return [0.0] + [close[i] / close[i - 1] - 1 for i in range(1, len(close))]


def held_from_pos(pos, delay=0):
    n = len(pos)
    held = [0] * n
    for t in range(n - 1 - delay):
        held[t + 1 + delay] = pos[t]
    return held


def simulate(held, ret, cost):
    """Per-bar net returns and the list of closed-trade returns (costs included)."""
    net, trades, prev, cur = [], [], 0, None
    for t in range(len(held)):
        r = held[t] * ret[t] - abs(held[t] - prev) * cost
        net.append(r)
        if held[t] == 1 and prev == 0:
            cur = 1.0
        if cur is not None:
            cur *= 1 + r
        if held[t] == 0 and prev == 1:
            trades.append(cur - 1); cur = None
        prev = held[t]
    if cur is not None:
        trades.append(cur - 1)
    return net, trades


def stats(net, trades, held, ppy):
    n = len(net)
    mean = sum(net) / n
    var = sum((x - mean) ** 2 for x in net) / n
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in net:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return dict(
        ret=eq - 1, cagr=eq ** (ppy / n) - 1 if eq > 0 else -1.0,
        sharpe=(mean / math.sqrt(var) * math.sqrt(ppy)) if var > 0 else 0.0,
        maxdd=dd, trades=len(trades), per_year=len(trades) / (n / ppy),
        win=(sum(t > 0 for t in trades) / len(trades)) if trades else 0.0,
        avg_trade=(sum(trades) / len(trades)) if trades else 0.0,
        exposure=sum(held) / n)


def segments(held):
    """[(a, b)) index ranges where held == 1, plus the number of position changes (a leading entry counts)."""
    segs, a = [], None
    for t, h in enumerate(held):
        if h and a is None:
            a = t
        if not h and a is not None:
            segs.append((a, t)); a = None
    if a is not None:
        segs.append((a, len(held)))
    changes = sum(2 if b < len(held) else 1 for _, b in segs)
    return segs, changes


class NullTest:
    """Data-snooping control by circularly shifting the RETURNS against fixed positions.

    A shift keeps each strategy's exposure, trade count, costs and the return series' own volatility clustering/drift, but
    destroys the timing relationship. The distribution of the best Sharpe over the whole grid under random shifts is what
    'looking at many strategies and keeping the best' produces by luck; the real best must beat it to mean anything.
    Uses prefix sums, so one shift costs O(#trades) per strategy.
    """
    def __init__(self, ret, ppy, cost):
        self.n, self.ppy, self.cost = len(ret), ppy, cost
        self.P, self.Q = [0.0], [0.0]
        for r in ret:
            self.P.append(self.P[-1] + r); self.Q.append(self.Q[-1] + r * r)

    def _circ(self, arr, a, b):
        n = self.n
        a %= n; b = a + (b - a)
        return arr[b] - arr[a] if b <= n else arr[n] - arr[a] + arr[b - n]

    def sharpe(self, segs, changes, shift=0):
        g = q = 0.0
        for a, b in segs:
            lo = (a + shift) % self.n
            hi = lo + (b - a)
            if hi <= self.n:
                g += self.P[hi] - self.P[lo]; q += self.Q[hi] - self.Q[lo]
            else:
                g += self.P[self.n] - self.P[lo] + self.P[hi - self.n]
                q += self.Q[self.n] - self.Q[lo] + self.Q[hi - self.n]
        mean = (g - changes * self.cost) / self.n
        var = q / self.n - mean * mean
        return mean / math.sqrt(var) * math.sqrt(self.ppy) if var > 0 else 0.0

    def run(self, seg_list, k=400, seed=7, min_shift=None):
        """seg_list: [(segs, changes)]. Returns (observed sharpes, null sharpes[k][strategy])."""
        rnd = random.Random(seed)
        lo = min_shift or max(24, self.n // 50)
        shifts = [rnd.randint(lo, self.n - lo) for _ in range(k)]
        obs = [self.sharpe(s, c) for s, c in seg_list]
        null = [[self.sharpe(s, c, sh) for s, c in seg_list] for sh in shifts]
        return obs, null


def p_best(obs, null):
    """Family-wise p-value: how often the best-of-grid Sharpe under random timing is at least the observed best."""
    ob = max(obs)
    return (1 + sum(max(row) >= ob for row in null)) / (len(null) + 1)


def p_single(obs_i, null, i):
    return (1 + sum(row[i] >= obs_i for row in null)) / (len(null) + 1)
