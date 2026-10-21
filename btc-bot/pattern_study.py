"""Which chart pattern has BTC actually been faithful to? Pre-registered protocol (fixed before any result was seen).

For every pattern signal (breakout close, known at that bar):
  * directional net return at +24h and +72h: d * (close[t+H]/close[t] - 1) - round-trip cost (taker fee + slippage, both sides);
  * textbook outcome: measured-move target vs. last opposing swing (stop), first touch within 72h, stop checked first
    when a bar touches both. Compared with the driftless random-walk expectation s/(s+g) of hitting the target first.
Null for the first metric: the SAME number of random entry times drawn from the same period, same direction, same horizon
(controls for BTC's upward drift: a bullish pattern must beat a random long, a bearish one a random short).
Signals of one (timeframe, scale, pattern, direction) are de-overlapped by 72h so they are independent trades.
Multiple testing: Benjamini-Hochberg q-values over all (timeframe, scale, pattern, direction) groups per period and horizon.
"""
import bisect, math, random, time
import data, engine as E, patterns as PT, strategies as S

RT = 2 * (E.FEE + E.SLIP)                   # round-trip cost
HOURS = (24, 72)
MAXHOLD_H = 72
SCALES = (3.0, 5.0)
TFS = (1, 4)                                # bar size in hours


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def bh_q(pvals):
    """Benjamini-Hochberg q-values (same order as the input)."""
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    q, prev = [0.0] * n, 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvals[i] * n / rank)
        q[i] = prev
    return q


def first_touch(sig, o, h, l, c, maxbars):
    """Returns ('target'|'stop'|'timeout', R multiple, expected P(target first) for a driftless walk)."""
    entry, stop, target, d = sig.entry, sig.stop, sig.target, sig.dir
    risk, gain = abs(entry - stop), abs(target - entry)
    if risk <= 0 or gain <= 0:
        return None
    exp_p = risk / (risk + gain)
    for j in range(sig.t + 1, min(len(c), sig.t + 1 + maxbars)):
        hit_stop = l[j] <= stop if d == 1 else h[j] >= stop
        hit_tgt = h[j] >= target if d == 1 else l[j] <= target
        if hit_stop:
            return "stop", -1.0, exp_p
        if hit_tgt:
            return "target", gain / risk, exp_p
    end = min(len(c) - 1, sig.t + maxbars)
    return "timeout", d * (c[end] - entry) / risk, exp_p


class Market:
    """Bars + precomputed forward returns for one timeframe."""
    def __init__(self, rows, factor):
        self.rows = rows
        self.ts = [r[0] for r in rows]
        self.o, self.h, self.l, self.c = ([r[i] for r in rows] for i in (1, 2, 3, 4))
        self.factor, self.n = factor, len(rows)
        self.hb = {H: max(1, H // factor) for H in HOURS}
        self.fr = {H: [(self.c[t + b] / self.c[t] - 1) if t + b < self.n else None for t in range(self.n)] for H, b in self.hb.items()}
        self.maxbars = MAXHOLD_H // factor

    def idx_from(self, ts_ms):
        return bisect.bisect_left(self.ts, ts_ms)


def deoverlap(sigs, maxbars):
    out, last = [], -10 ** 9
    for s in sorted(sigs, key=lambda s: s.t):
        if s.t >= last + maxbars:
            out.append(s); last = s.t
    return out


def collect(market, mult):
    """Signals grouped by (pattern, dir) and de-overlapped."""
    sigs = PT.detect(market.o, market.h, market.l, market.c, mult=mult)
    groups = {}
    for s in sigs:
        groups.setdefault((s.name, s.dir), []).append(s)
    return {k: deoverlap(v, market.maxbars) for k, v in groups.items()}


def null_means(market, H, d, n, lo, hi, draws, rnd):
    """Distribution of the mean directional net return of n random entries in [lo, hi) bars."""
    fr = market.fr[H]
    pool = [t for t in range(lo, hi) if fr[t] is not None]
    if len(pool) < n or n == 0:
        return []
    out = []
    for _ in range(draws):
        out.append(sum(d * fr[t] - RT for t in rnd.sample(pool, n)) / n)
    return out


def analyze(market, groups, lo, hi, draws=1500, seed=1):
    """Stats per group inside bars [lo, hi). Returns list of dicts."""
    rnd = random.Random(seed)
    rows = []
    for (name, d), sigs in sorted(groups.items()):
        sg = [s for s in sigs if lo <= s.t < hi]
        for H in HOURS:
            fr = market.fr[H]
            use = [s for s in sg if fr[s.t] is not None]
            n = len(use)
            if n == 0:
                continue
            r = [d * fr[s.t] - RT for s in use]
            gross_hit = sum(d * fr[s.t] > 0 for s in use)
            nm = null_means(market, H, d, n, lo, hi, draws, rnd)
            mean = sum(r) / n
            p = (1 + sum(x >= mean for x in nm)) / (len(nm) + 1) if nm else float("nan")
            nh = sum(1 for x in nm) and sum(nm) / len(nm)
            rows.append(dict(pattern=name, dir=d, H=H, n=n, mean=mean, hit=gross_hit / n, hit_ci=wilson(gross_hit, n),
                             null_mean=(sum(nm) / len(nm)) if nm else float("nan"), p=p))
    return rows


def textbook(market, groups, lo, hi):
    out = []
    for (name, d), sigs in sorted(groups.items()):
        res = []
        for s in (x for x in sigs if lo <= x.t < hi):
            r = first_touch(s, market.o, market.h, market.l, market.c, market.maxbars)
            if r:
                res.append(r)
        resolved = [x for x in res if x[0] != "timeout"]
        if len(resolved) < 5:
            continue
        k = sum(x[0] == "target" for x in resolved)
        e = sum(x[2] for x in resolved) / len(resolved)
        var = sum(x[2] * (1 - x[2]) for x in resolved)
        z = (k - sum(x[2] for x in resolved)) / math.sqrt(var) if var > 0 else 0.0
        out.append(dict(pattern=name, dir=d, n=len(res), resolved=len(resolved), target_rate=k / len(resolved), expected=e,
                        z=z, meanR=sum(x[1] for x in res) / len(res)))
    return out


MIN_N = 8                                   # groups with fewer independent signals are not tested at all


def run(period_years=2.0, draws=1500, out=print, min_n=MIN_N):
    rows1h = data.load("60m")
    now_ms = rows1h[-1][0]
    cut_ms = now_ms - int(period_years * 365.25 * 24 * 3600 * 1000)
    mk = {1: Market(rows1h, 1), 4: Market(S.resample(rows1h, 4), 4)}
    table = {}
    for tf in TFS:
        m = mk[tf]
        for mult in SCALES:
            table[(tf, mult)] = collect(m, mult)
    out(f"BTCUSDT {time.strftime('%Y-%m-%d', time.gmtime(rows1h[0][0]/1000))} .. {time.strftime('%Y-%m-%d', time.gmtime(now_ms/1000))}; "
        f"'recent' = last {period_years:g} years from {time.strftime('%Y-%m-%d', time.gmtime(cut_ms/1000))}; round-trip cost {RT*100:.2f}%")
    results = {}
    for label, recent in (("FULL HISTORY", False), (f"LAST {period_years:g} YEARS", True)):
        allrows = []
        for (tf, mult), groups in table.items():
            m = mk[tf]
            lo = m.idx_from(cut_ms) if recent else 0
            for r in analyze(m, groups, lo, m.n, draws):
                r.update(tf=tf, mult=mult)
                allrows.append(r)
            for r in textbook(m, groups, lo, m.n):
                results.setdefault((label, "tb"), []).append(dict(r, tf=tf, mult=mult))
        for H in HOURS:
            sub = [r for r in allrows if r["H"] == H and r["p"] == r["p"] and r["n"] >= min_n]    # the FDR family
            for r, q in zip(sub, bh_q([r["p"] for r in sub])):
                r["q"] = q
        results[(label, "dir")] = allrows
    return results, mk


def show(results, out=print, min_n=8, top=12):
    for label in sorted({k[0] for k in results}, key=lambda s: s != "FULL HISTORY"):
        rows = results[(label, "dir")]
        for H in HOURS:
            sub = [r for r in rows if r["H"] == H and r["n"] >= min_n]
            sub.sort(key=lambda r: r["mean"] - r["null_mean"], reverse=True)
            tot = len([r for r in rows if r["H"] == H and r["p"] == r["p"] and r["n"] >= min_n])
            out(f"\n=== {label}, +{H}h directional NET return, groups with n >= {min_n} (of {tot} groups tested), ranked by excess over random timing ===")
            out(f"{'tf':>3s} {'scale':>5s} {'pattern':20s} {'dir':>4s} {'n':>4s} {'mean':>7s} {'random':>7s} {'excess':>7s} {'dir-hit':>8s} {'95% CI':>13s} {'p':>6s} {'FDR q':>6s}")
            for r in sub[:top]:
                out(f"{r['tf']:>2d}h {r['mult']:5.1f} {r['pattern']:20s} {('long' if r['dir']==1 else 'short'):>4s} {r['n']:4d} {r['mean']*100:+6.2f}% {r['null_mean']*100:+6.2f}% "
                    f"{(r['mean']-r['null_mean'])*100:+6.2f}% {r['hit']*100:7.0f}% [{r['hit_ci'][0]*100:3.0f},{r['hit_ci'][1]*100:3.0f}]% {r['p']:6.3f} {r.get('q', float('nan')):6.3f}")
            sig = [r for r in rows if r["H"] == H and r["n"] >= min_n and r.get("q", 1) < 0.10]
            out(f"  groups with FDR q < 0.10: {len(sig)}" + (": " + ", ".join(f"{r['tf']}h/{r['mult']:g}/{r['pattern']}/{'L' if r['dir']==1 else 'S'}" for r in sig) if sig else ""))
        tb = results.get((label, "tb"), [])
        out(f"\n--- {label}: textbook outcome (measured-move target vs swing stop, first touch within {MAXHOLD_H}h; resolved trades only) ---")
        out(f"{'tf':>3s} {'scale':>5s} {'pattern':20s} {'dir':>4s} {'n':>4s} {'resolved':>8s} {'target-first':>12s} {'coin-flip':>9s} {'z':>6s} {'mean R':>7s}")
        for r in sorted(tb, key=lambda r: -r["z"])[:top]:
            out(f"{r['tf']:>2d}h {r['mult']:5.1f} {r['pattern']:20s} {('long' if r['dir']==1 else 'short'):>4s} {r['n']:4d} {r['resolved']:8d} {r['target_rate']*100:11.0f}% {r['expected']*100:8.0f}% {r['z']:+6.2f} {r['meanR']:+7.2f}")


def show_pattern(results, name, out=print, min_n=1):
    """Every configuration of one pattern, both periods and horizons, nothing hidden."""
    out(f"\n##### {name}: all configurations #####")
    for label in sorted({k[0] for k in results}, key=lambda s: s != "FULL HISTORY"):
        out(f"-- {label}")
        out(f"{'tf':>3s} {'scale':>5s} {'dir':>5s} {'H':>4s} {'n':>4s} {'mean':>7s} {'random':>7s} {'excess':>7s} {'dir-hit':>8s} {'p':>6s}")
        for r in sorted((r for r in results[(label, 'dir')] if r['pattern'] == name and r['n'] >= min_n), key=lambda r: (r['tf'], r['mult'], r['H'])):
            out(f"{r['tf']:>2d}h {r['mult']:5.1f} {('long' if r['dir']==1 else 'short'):>5s} {r['H']:>3d}h {r['n']:4d} {r['mean']*100:+6.2f}% {r['null_mean']*100:+6.2f}% "
                f"{(r['mean']-r['null_mean'])*100:+6.2f}% {r['hit']*100:7.0f}% {r['p']:6.3f}" + ("   (n<%d: not in the FDR family)" % MIN_N if r['n'] < MIN_N else ""))


def cross_patterns(symbols, draws=1000, out=print, min_total=30, top=12):
    """Same frozen rules on other coins (4h bars). Per (pattern, dir, scale, horizon): sample-weighted excess over random
    timing, number of coins where it was positive, Stouffer-combined p (coins are correlated, so this is optimistic)."""
    from statistics import NormalDist
    nd = NormalDist()
    acc = {}
    used = []
    for sym in symbols:
        rows = data.load("4h", sym)
        if len(rows) < 3000:
            continue
        used.append(sym)
        m = Market(rows, 4)
        for mult in SCALES:
            groups = collect(m, mult)
            for r in analyze(m, groups, 0, m.n, draws, seed=hash((sym, mult)) % 10 ** 6):
                acc.setdefault((r["pattern"], r["dir"], mult, r["H"]), []).append(dict(r, sym=sym))
    res = []
    for key, rs in acc.items():
        rs = [r for r in rs if r["n"] >= 3 and r["p"] == r["p"]]
        n = sum(r["n"] for r in rs)
        if n < min_total or len(rs) < 3:
            continue
        ex = sum(r["n"] * (r["mean"] - r["null_mean"]) for r in rs) / n
        z = sum(math.sqrt(r["n"]) * nd.inv_cdf(min(1 - 1e-4, max(1e-4, 1 - r["p"]))) for r in rs) / math.sqrt(sum(r["n"] for r in rs))
        res.append(dict(key=key, assets=len(rs), n=n, excess=ex, pos=sum(r["mean"] > r["null_mean"] for r in rs), p=1 - nd.cdf(z)))
    for H in HOURS:
        sub = [r for r in res if r["key"][3] == H]
        for r, q in zip(sub, bh_q([r["p"] for r in sub])):
            r["q"] = q
    out(f"\n=== cross-asset (4h, {len(used)} coins: {', '.join(used)}), pooled; groups with >= {min_total} signals over >= 3 coins ===")
    for H in HOURS:
        sub = sorted((r for r in res if r["key"][3] == H), key=lambda r: r["p"])
        out(f"\n+{H}h, {len(sub)} groups in the FDR family, {sum(r['q'] < 0.10 for r in sub)} with q < 0.10")
        out(f"{'scale':>5s} {'pattern':20s} {'dir':>5s} {'coins':>5s} {'n':>5s} {'excess':>8s} {'coins>0':>8s} {'p':>7s} {'q':>6s}")
        for r in sub[:top]:
            pat, d, mult, _ = r["key"]
            out(f"{mult:5.1f} {pat:20s} {('long' if d==1 else 'short'):>5s} {r['assets']:5d} {r['n']:5d} {r['excess']*100:+7.2f}% {r['pos']:>4d}/{r['assets']:<3d} {r['p']:7.4f} {r['q']:6.3f}")
    return res


def confirm(symbols, pattern, d, mult, H, label, out=print, k_match=200):
    """Pre-registered single-hypothesis test of one pattern group on 4h bars.
    excess = directional net return - the coin's own average over all bars (random timing);
    excess_matched = ... - the average over the k bars of the same coin whose trailing 7-day return is closest to the signal's
    (so 'buying after a decline' is not credited to the pattern).
    Inference is clustered by calendar week across coins (signals on different coins in one week count once)."""
    from statistics import NormalDist
    week_ms = 7 * 86400 * 1000
    rows_out = []
    per_coin = []
    for sym in symbols:
        rows = data.load("4h", sym)
        if len(rows) < 3000:
            continue
        m = Market(rows, 4)
        fr, n = m.fr[H], m.n
        net = [d * fr[t] - RT if fr[t] is not None else None for t in range(n)]
        valid = [t for t in range(n) if net[t] is not None]
        base = sum(net[t] for t in valid) / len(valid)
        look = 42                                                        # 7 days of 4h bars
        tr = {t: m.c[t] / m.c[t - look] - 1 for t in range(look, n)}
        order = sorted((t for t in valid if t in tr), key=lambda t: tr[t])
        trs = [tr[t] for t in order]
        sigs = collect(m, mult).get((pattern, d), [])
        got = 0
        for s in sigs:
            if net[s.t] is None or s.t not in tr:
                continue
            pos = bisect.bisect_left(trs, tr[s.t])
            lo = max(0, min(pos - k_match // 2, len(order) - k_match))
            nb = [net[order[j]] for j in range(lo, min(len(order), lo + k_match))]
            rows_out.append(dict(sym=sym, ts=m.ts[s.t], net=net[s.t], ex=net[s.t] - base, exm=net[s.t] - sum(nb) / len(nb)))
            got += 1
        per_coin.append((sym, got))
    if not rows_out:
        out(f"{label}: no signals"); return None
    nd = NormalDist()

    def stats_for(rs, key):
        weeks = {}
        for r in rs:
            weeks.setdefault(r["ts"] // week_ms, []).append(r[key])
        wm = [sum(v) / len(v) for v in weeks.values()]
        if len(wm) < 3:
            return len(rs), len(wm), float("nan"), float("nan"), float("nan")
        mu = sum(wm) / len(wm)
        sd = math.sqrt(sum((x - mu) ** 2 for x in wm) / (len(wm) - 1))
        t = mu / (sd / math.sqrt(len(wm))) if sd > 0 else 0.0
        return len(rs), len(wm), mu, t, 1 - nd.cdf(t)
    rows_out.sort(key=lambda r: r["ts"])
    mid = rows_out[len(rows_out) // 2]["ts"]
    out(f"\n--- {label}: {pattern} {'long' if d == 1 else 'short'}, scale {mult:g}, +{H}h, {len(per_coin)} coins ---")
    out(f"{'subset':14s} {'signals':>7s} {'weeks':>5s} {'mean net':>9s} | {'excess vs random':>22s} | {'excess vs 7d-return-matched':>30s}")
    for name, rs in (("all", rows_out), ("first half", [r for r in rows_out if r["ts"] < mid]), ("second half", [r for r in rows_out if r["ts"] >= mid])):
        n1, w1, m1, t1, p1 = stats_for(rs, "ex")
        n2, w2, m2, t2, p2 = stats_for(rs, "exm")
        mean_net = sum(r["net"] for r in rs) / len(rs)
        out(f"{name:14s} {n1:7d} {w1:5d} {mean_net*100:+8.2f}% | {m1*100:+7.2f}%  t={t1:+5.2f} p={p1:6.4f} | {m2*100:+7.2f}%  t={t2:+5.2f} p={p2:6.4f}")
    return rows_out


def trade_pattern(market, sigs, cost, maxbars, t_from=0, geometry=None):
    """Long-only, one position at a time: enter at the signal close, exit at the swing stop, the measured-move target, or after
    maxbars (close). Returns (equity multiplier, list of trade returns, bars in market). `geometry` lets the null reuse real
    stop/target distances at random entry bars."""
    c, h, l = market.c, market.h, market.l
    eq, trades, busy_until, bars_in = 1.0, [], -1, 0
    for s in sigs:
        if s.t < t_from or s.t <= busy_until or s.dir != 1:
            continue
        entry, stop, target = s.entry, s.stop, s.target
        exit_px, j = c[min(len(c) - 1, s.t + maxbars)], min(len(c) - 1, s.t + maxbars)
        for k in range(s.t + 1, min(len(c), s.t + 1 + maxbars)):
            if l[k] <= stop:
                exit_px, j = stop, k; break
            if h[k] >= target:
                exit_px, j = target, k; break
        r = exit_px / entry - 1 - cost
        eq *= 1 + r
        trades.append(r)
        busy_until = j
        bars_in += j - s.t
    return eq, trades, bars_in


def pattern_trading(symbol="BTCUSDT", out=print, cost=RT, draws=400, names=PT.LONG_NAMES, seed=3):
    """Does trading the long patterns pay on BTC? Compared with random entries that reuse the same stop/target geometry."""
    rows1h = data.load("60m")
    res = {}
    out(f"\n=== long-pattern trading on {symbol}, round-trip cost {cost*100:.2f}%, exit at stop / measured-move target / 72h ===")
    out(f"{'tf':>3s} {'scale':>5s} {'patterns':26s} {'trades':>6s} {'tr/yr':>6s} {'win':>4s} {'avg trade':>9s} {'CAGR':>8s} {'B&H CAGR':>9s} {'in mkt':>6s} {'random-entry CAGR (median / 95th)':>34s} {'p':>6s}")
    for tf in TFS:
        m = Market(rows1h if tf == 1 else S.resample(rows1h, 4), tf)
        years = (m.ts[-1] - m.ts[0]) / 3.15576e10
        bh = (m.c[-1] / m.c[0]) ** (1 / years) - 1
        for mult in SCALES:
            allsig = [s for s in PT.detect(m.o, m.h, m.l, m.c, mult=mult) if s.dir == 1 and s.name in names]
            for label, subset in (("all long patterns", names), ("falling_wedge", ("falling_wedge",))):
                sg = sorted([s for s in allsig if s.name in subset], key=lambda s: s.t)
                if len(sg) < 10:
                    continue
                eq, tr, bars_in = trade_pattern(m, sg, cost, m.maxbars)
                if not tr:
                    continue
                cagr = eq ** (1 / years) - 1
                geo = [(abs(s.entry - s.stop) / s.entry, abs(s.target - s.entry) / s.entry) for s in sg]
                rnd = random.Random(seed)
                null = []
                for _ in range(draws):
                    # same number of attempted signals at random bars, same stop/target distances, same sequential rules
                    ts_ = sorted(rnd.randrange(60, m.n - m.maxbars - 1) for _ in range(len(sg)))
                    fake = []
                    for t in ts_:
                        sp, gp = rnd.choice(geo)
                        e = m.c[t]
                        fake.append(PT.Signal(t, "rand", 1, e, e * (1 - sp), e * (1 + gp), t, t))
                    e2, _, _ = trade_pattern(m, fake, cost, m.maxbars)
                    null.append(e2 ** (1 / years) - 1)
                null.sort()
                p = (1 + sum(x >= cagr for x in null)) / (len(null) + 1)
                out(f"{tf:>2d}h {mult:5.1f} {label:26s} {len(tr):6d} {len(tr)/years:6.1f} {sum(x > 0 for x in tr)/len(tr)*100:3.0f}% {sum(tr)/len(tr)*100:+8.2f}% {cagr*100:+7.1f}% {bh*100:+8.1f}% "
                    f"{bars_in/m.n*100:5.1f}% {null[len(null)//2]*100:+14.1f}% / {null[int(len(null)*.95)]*100:+6.1f}% {p:6.3f}")
                res[(tf, mult, label)] = dict(cagr=cagr, trades=len(tr), p=p, avg=sum(tr) / len(tr))
    return res
