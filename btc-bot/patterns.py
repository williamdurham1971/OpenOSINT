"""Classic chart patterns, detected causally (no look-ahead) from OHLC bars.

Swing pivots come from an ATR-scaled zig-zag: a swing high is only *known* once price has fallen `mult x ATR` below it, so a
pivot is available `conf` bars after it happened. Patterns are built from the last few confirmed pivots and are *armed*; they
fire a Signal on the first bar whose CLOSE breaks the boundary by 0.25 ATR, and are dropped if the opposite boundary breaks
first or they expire. Everything uses data up to and including the signal bar only (selftest checks this).

Rules are fixed here, before any result was looked at. thr = mult x ATR(14) at the pivot-confirmation bar (the "swing size").

  falling wedge   both lines fall, upper falls faster, range converges   -> long on a close above the upper line
  rising wedge    both lines rise, lower rises faster, range converges   -> short on a close below the lower line
  ascending tri.  flat top, rising lows                                  -> long above the top
  descending tri. flat bottom, falling highs                             -> short below the bottom
  symmetrical tri falling highs + rising lows                            -> whichever side breaks first
  double bottom / double top   two equal extremes + neckline             -> long / short on the neckline break
  inverse H&S / head & shoulders  three extremes, middle one beyond both -> long / short on the neckline break
  bull / bear flag  impulse leg then a shallow counter-trend drift       -> trade with the impulse on the flag break
Targets are measured moves, stops are the last opposing swing; both are used only by the studies, never to filter signals.
"""
from dataclasses import dataclass

MIN_SPAN, MAX_SPAN = 10, 150        # bars between the first and last pivot of a wedge/triangle
BUF = 0.25                          # breakout buffer in ATRs
NAMES = ("falling_wedge", "rising_wedge", "asc_triangle", "desc_triangle", "sym_triangle", "double_bottom", "double_top",
         "inv_head_shoulders", "head_shoulders", "bull_flag", "bear_flag")
LONG_NAMES = ("falling_wedge", "asc_triangle", "double_bottom", "inv_head_shoulders", "bull_flag")


@dataclass
class Signal:
    t: int            # bar index of the breakout close (the signal is known at this bar's close)
    name: str
    dir: int          # +1 long / -1 short
    entry: float      # close[t]
    stop: float
    target: float
    start: int        # index of the first pivot used
    last: int         # index of the last pivot used


def wilder_atr(h, l, c, n=14):
    out, prev, atr = [None] * len(c), None, None
    trs = []
    for i in range(len(c)):
        tr = h[i] - l[i] if prev is None else max(h[i] - l[i], abs(h[i] - prev), abs(l[i] - prev))
        prev = c[i]
        if atr is None:
            trs.append(tr)
            if len(trs) == n:
                atr = sum(trs) / n
                out[i] = atr
        else:
            atr = (atr * (n - 1) + tr) / n
            out[i] = atr
    return out


class Pivot:
    __slots__ = ("i", "p", "k", "c")

    def __init__(self, i, p, k, c):
        self.i, self.p, self.k, self.c = i, p, k, c        # bar, price, 'H'/'L', bar at which it became known


class ZigZag:
    """Causal ATR zig-zag. Feed bars one at a time; update() returns a Pivot when one is confirmed on this bar."""
    def __init__(self, mult):
        self.mult, self.dir = mult, 0
        self.hi = self.lo = None
        self.hi_i = self.lo_i = 0

    def update(self, t, h, l, atr):
        if self.hi is None:
            self.hi, self.lo, self.hi_i, self.lo_i = h, l, t, t
            return None
        if atr is None:
            if h > self.hi: self.hi, self.hi_i = h, t
            if l < self.lo: self.lo, self.lo_i = l, t
            return None
        thr = self.mult * atr
        if self.dir == 0:
            if h > self.hi: self.hi, self.hi_i = h, t
            if l < self.lo: self.lo, self.lo_i = l, t
            if h - self.lo >= thr:                                   # first leg is up: the low was a swing low
                p = Pivot(self.lo_i, self.lo, "L", t); self.dir = 1; self.hi, self.hi_i = h, t
                return p
            if self.hi - l >= thr:
                p = Pivot(self.hi_i, self.hi, "H", t); self.dir = -1; self.lo, self.lo_i = l, t
                return p
            return None
        if self.dir == 1:                                            # following an up-leg
            if h > self.hi:
                self.hi, self.hi_i = h, t
                return None
            if self.hi - l >= thr:
                p = Pivot(self.hi_i, self.hi, "H", t); self.dir = -1; self.lo, self.lo_i = l, t
                return p
            return None
        if l < self.lo:
            self.lo, self.lo_i = l, t
            return None
        if h - self.lo >= thr:
            p = Pivot(self.lo_i, self.lo, "L", t); self.dir = 1; self.hi, self.hi_i = h, t
            return p
        return None


def line(points):
    """Returns f(i) for the least-squares line through the points."""
    n = len(points)
    mx = sum(i for i, _ in points) / n
    my = sum(p for _, p in points) / n
    sxx = sum((i - mx) ** 2 for i, _ in points)
    m = sum((i - mx) * (p - my) for i, p in points) / sxx if sxx else 0.0
    return lambda i, m=m, mx=mx, my=my: my + m * (i - mx), m


def flat(level):
    return lambda i, v=level: v


def _armed(name, last, start, expire, up=None, dn=None, inval_up=None, inval_dn=None, stop_long=None, stop_short=None, tgt=0.0):
    return dict(name=name, last=last, start=start, expire=expire, up=up, dn=dn, inval_up=inval_up, inval_dn=inval_dn,
                stop_long=stop_long, stop_short=stop_short, tgt=tgt)


def _channel_patterns(P, t, thr):
    """Wedges and triangles from the last 4..7 pivots. Each window size can arm a pattern; per pattern name the largest wins."""
    found = {}
    for k in (7, 6, 5, 4):
        if len(P) < k:
            continue
        S = P[-k:]
        Hs = [(x.i, x.p) for x in S if x.k == "H"]
        Ls = [(x.i, x.p) for x in S if x.k == "L"]
        if len(Hs) < 2 or len(Ls) < 2:
            continue
        i0, i1 = S[0].i, S[-1].i
        span = i1 - i0
        if not MIN_SPAN <= span <= MAX_SPAN:
            continue
        fu, mu = line(Hs)
        fl, ml = line(Ls)
        if any(abs(p - fu(i)) > 0.5 * thr for i, p in Hs) or any(abs(p - fl(i)) > 0.5 * thr for i, p in Ls):
            continue
        w0, w1, wt = fu(i0) - fl(i0), fu(i1) - fl(i1), fu(t) - fl(t)
        if w0 < 1.5 * thr or w1 <= 0 or wt <= 0 or w1 / w0 > 0.7:
            continue
        du, dl = mu * span / thr, ml * span / thr
        last_low = max((x for x in S if x.k == "L"), key=lambda x: x.i).p
        last_high = max((x for x in S if x.k == "H"), key=lambda x: x.i).p
        ex = i1 + max(span, MIN_SPAN)
        kw = dict(last=i1, start=i0, expire=ex, tgt=w0)
        cand = None
        if du <= -0.8 and dl <= -0.3 and du < dl:
            cand = _armed("falling_wedge", up=fu, inval_dn=fl, stop_long=last_low, **kw)
        elif dl >= 0.8 and du >= 0.3 and dl > du:
            cand = _armed("rising_wedge", dn=fl, inval_up=fu, stop_short=last_high, **kw)
        elif abs(du) <= 0.4 and dl >= 0.5:
            cand = _armed("asc_triangle", up=fu, inval_dn=fl, stop_long=last_low, **kw)
        elif abs(dl) <= 0.4 and du <= -0.5:
            cand = _armed("desc_triangle", dn=fl, inval_up=fu, stop_short=last_high, **kw)
        elif du <= -0.5 and dl >= 0.5:
            cand = _armed("sym_triangle", up=fu, dn=fl, stop_long=last_low, stop_short=last_high, **kw)
        if cand is not None and cand["name"] not in found:        # k runs from the largest window down
            found[cand["name"]] = cand
    return list(found.values())


def _double(P, t, thr):
    out = []
    if len(P) >= 3:
        a, b, c = P[-3:]
        gap = c.i - a.i
        if a.k == "L" and b.k == "H" and c.k == "L" and 10 <= gap <= MAX_SPAN and abs(a.p - c.p) <= 0.5 * thr and b.p - max(a.p, c.p) >= 1.5 * thr:
            lo = min(a.p, c.p)
            out.append(_armed("double_bottom", last=c.i, start=a.i, expire=c.i + gap, up=flat(b.p), inval_dn=flat(lo), stop_long=lo, tgt=b.p - lo))
        if a.k == "H" and b.k == "L" and c.k == "H" and 10 <= gap <= MAX_SPAN and abs(a.p - c.p) <= 0.5 * thr and min(a.p, c.p) - b.p >= 1.5 * thr:
            hi = max(a.p, c.p)
            out.append(_armed("double_top", last=c.i, start=a.i, expire=c.i + gap, dn=flat(b.p), inval_up=flat(hi), stop_short=hi, tgt=hi - b.p))
    return out


def _head_shoulders(P, t, thr):
    out = []
    if len(P) >= 5:
        a, b, c, d, e = P[-5:]
        span = e.i - a.i
        if not 20 <= span <= MAX_SPAN:
            return out
        if (a.k, b.k, c.k, d.k, e.k) == ("H", "L", "H", "L", "H") and c.p - max(a.p, e.p) >= 0.5 * thr and abs(a.p - e.p) <= 0.6 * (c.p - (b.p + d.p) / 2):
            neck, _ = line([(b.i, b.p), (d.i, d.p)])
            h = c.p - neck(c.i)
            out.append(_armed("head_shoulders", last=e.i, start=a.i, expire=e.i + span // 2, dn=neck, inval_up=flat(e.p), stop_short=e.p, tgt=h))
        if (a.k, b.k, c.k, d.k, e.k) == ("L", "H", "L", "H", "L") and min(a.p, e.p) - c.p >= 0.5 * thr and abs(a.p - e.p) <= 0.6 * ((b.p + d.p) / 2 - c.p):
            neck, _ = line([(b.i, b.p), (d.i, d.p)])
            h = neck(c.i) - c.p
            out.append(_armed("inv_head_shoulders", last=e.i, start=a.i, expire=e.i + span // 2, up=neck, inval_dn=flat(e.p), stop_long=e.p, tgt=h))
    return out


def _flags(P, t, thr):
    out = []
    if len(P) >= 4:
        a, b, c, d = P[-4:]
        leg = b.i - a.i
        if (a.k, b.k, c.k, d.k) == ("L", "H", "L", "H") and 2 <= leg <= 30 and b.p - a.p >= 2.5 * thr \
                and c.p >= a.p + 0.5 * (b.p - a.p) and d.p <= b.p and c.p < b.p:
            f, _ = line([(b.i, b.p), (d.i, d.p)])
            out.append(_armed("bull_flag", last=d.i, start=a.i, expire=d.i + 40, up=f, inval_dn=flat(a.p + 0.5 * (b.p - a.p)), stop_long=c.p, tgt=b.p - a.p))
        if (a.k, b.k, c.k, d.k) == ("H", "L", "H", "L") and 2 <= leg <= 30 and a.p - b.p >= 2.5 * thr \
                and c.p <= a.p - 0.5 * (a.p - b.p) and d.p >= b.p and c.p > b.p:
            f, _ = line([(b.i, b.p), (d.i, d.p)])
            out.append(_armed("bear_flag", last=d.i, start=a.i, expire=d.i + 40, dn=f, inval_up=flat(a.p - 0.5 * (a.p - b.p)), stop_short=c.p, tgt=a.p - b.p))
    return out


def detect(o, h, l, c, mult=3.0, names=NAMES):
    """All signals on these bars, ordered by bar. Causal: a signal at bar t uses only bars <= t."""
    atr = wilder_atr(h, l, c)
    zz, piv, active, fired, sigs = ZigZag(mult), [], [], set(), []
    for t in range(len(c)):
        a_t = atr[t]
        buf = BUF * a_t if a_t is not None else None
        keep, fired_now = [], set()
        for a in active:                                              # 1) breakouts of patterns armed on EARLIER bars
            if buf is None or t > a["expire"]:
                continue
            if a["up"] is not None and c[t] > a["up"](t) + buf:
                if (a["name"], 1) not in fired_now:
                    fired_now.add((a["name"], 1))
                    sigs.append(Signal(t, a["name"], 1, c[t], a["stop_long"] if a["stop_long"] is not None else c[t] - a["tgt"], c[t] + a["tgt"], a["start"], a["last"]))
                continue
            if a["dn"] is not None and c[t] < a["dn"](t) - buf:
                if (a["name"], -1) not in fired_now:
                    fired_now.add((a["name"], -1))
                    sigs.append(Signal(t, a["name"], -1, c[t], a["stop_short"] if a["stop_short"] is not None else c[t] + a["tgt"], c[t] - a["tgt"], a["start"], a["last"]))
                continue
            if (a["inval_up"] is not None and c[t] > a["inval_up"](t) + buf) or (a["inval_dn"] is not None and c[t] < a["inval_dn"](t) - buf):
                continue
            keep.append(a)
        active = keep
        p = zz.update(t, h[t], l[t], a_t)                             # 2) a pivot confirmed on this bar arms new patterns
        if p is not None and a_t is not None:
            piv.append(p)
            thr = mult * a_t
            for a in _channel_patterns(piv, t, thr) + _double(piv, t, thr) + _head_shoulders(piv, t, thr) + _flags(piv, t, thr):
                key = (a["name"], a["start"], a["last"])
                if a["name"] in names and key not in fired:
                    fired.add(key); active.append(a)
    return sigs
