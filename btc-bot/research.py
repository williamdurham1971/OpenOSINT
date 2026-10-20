"""research: does any textbook technical strategy beat random timing on MEXC BTCUSDT after fees?

Protocol (fixed before looking at results):
  1. grid of 24 strategies x {1h, 4h} bars, long/flat spot, taker fee + slippage on every trade;
  2. selection uses ONLY the first 60% of history; the last 40% is looked at once, for the selected strategy;
  3. a circular-shift test asks how often random timing with the SAME exposure produces a best Sharpe this high
     (family-wise, so trying 24 strategies is accounted for); Bonferroni x2 for the two timeframes.
"""
import math, time
import data, engine as E, strategies as S


def fmt_pct(x):
    return f"{x*100:+7.1f}%"


def date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def evaluate(rows, factor, delay, fee, slip, k, split_frac=0.6, out=print):
    rr = rows if factor == 1 else S.resample(rows, factor)
    ts, o, h, l, c = ([r[i] for r in rr] for i in range(5))
    n, ppy, cost = len(c), 8760 / factor, fee + slip
    ret = E.returns(c)
    split = int(n * split_frac)
    out(f"\n=== {factor}h bars: {n} bars {date(ts[0])} .. {date(ts[-1])}; train {date(ts[0])}..{date(ts[split-1])}, "
        f"test {date(ts[split])}..{date(ts[-1])}; fee {fee*100:.3f}% + slip {slip*100:.3f}% per side, delay {delay} bar(s)")
    held_bh = [0] + [1] * (n - 1)
    rows_out, segs_all = [], []
    for fam, params in S.GRID:
        held = E.held_from_pos(S.positions(fam, params, o, h, l, c), delay)
        net, tr = E.simulate(held, ret, cost)
        full = E.stats(net, tr, held, ppy)
        ntr, trtr = E.simulate(held[:split], ret[:split], cost)
        nte, trte = E.simulate(held[split:], ret[split:], cost)
        rows_out.append(dict(name=S.name_of(fam, params), held=held, full=full,
                             train=E.stats(ntr, trtr, held[:split], ppy), test=E.stats(nte, trte, held[split:], ppy)))
        segs_all.append(E.segments(held))
    net, tr = E.simulate(held_bh, ret, cost)
    bh = E.stats(net, tr, held_bh, ppy)
    bh_tr = E.stats(*E.simulate(held_bh[:split], ret[:split], cost), held_bh[:split], ppy)
    bh_te = E.stats(*E.simulate(held_bh[split:], ret[split:], cost), held_bh[split:], ppy)
    out(f"{'strategy':36s} {'tr/yr':>6s} {'expo':>5s} {'CAGR':>8s} {'maxDD':>6s} {'Sharpe':>6s} {'train':>6s} {'test':>6s}")
    for r in rows_out:
        f = r["full"]
        out(f"{r['name']:36s} {f['per_year']:6.0f} {f['exposure']*100:4.0f}% {fmt_pct(f['cagr'])} {f['maxdd']*100:5.0f}% {f['sharpe']:6.2f} {r['train']['sharpe']:6.2f} {r['test']['sharpe']:6.2f}")
    out(f"{'BUY & HOLD':36s} {bh['per_year']:6.0f} {bh['exposure']*100:4.0f}% {fmt_pct(bh['cagr'])} {bh['maxdd']*100:5.0f}% {bh['sharpe']:6.2f} {bh_tr['sharpe']:6.2f} {bh_te['sharpe']:6.2f}")
    # 1) family-wise test on the full sample
    nt = E.NullTest(ret, ppy, cost)
    obs, null = nt.run(segs_all, k=k)
    best_i = max(range(len(obs)), key=lambda i: obs[i])
    p_fam = min(1.0, 2 * E.p_best(obs, null))
    ex = [sum(row[i] for row in null) / len(null) for i in range(len(obs))]
    out(f"\nfamily-wise test (best of {len(obs)}): best is {rows_out[best_i]['name']} with Sharpe {obs[best_i]:.2f}; "
        f"random timing with the same exposure gets a best-of-{len(obs)} of {sorted(max(r) for r in null)[len(null)//2]:.2f} (median), "
        f"{sorted(max(r) for r in null)[int(len(null)*.95)]:.2f} (95th pct)  ->  p = {p_fam:.3f} (Bonferroni x2)")
    # 2) pre-registered selection on train, one look at test
    elig = [r for r in rows_out if r["train"]["trades"] >= 30]
    sel = max(elig, key=lambda r: r["train"]["sharpe"])
    i = rows_out.index(sel)
    nt_te = E.NullTest(ret[split:], ppy, cost)
    segs_te = E.segments(sel["held"][split:])
    o_te, null_te = nt_te.run([segs_te], k=k)
    p_te = E.p_single(o_te[0], null_te, 0)
    t_ = sel["test"]
    out(f"selected on TRAIN only: {sel['name']} (train Sharpe {sel['train']['sharpe']:.2f}). On TEST: CAGR {fmt_pct(t_['cagr'])}, "
        f"maxDD {t_['maxdd']*100:.0f}%, Sharpe {t_['sharpe']:.2f}, {t_['trades']} trades, win {t_['win']*100:.0f}%, avg trade {t_['avg_trade']*100:+.2f}%; "
        f"buy&hold on TEST: CAGR {fmt_pct(bh_te['cagr'])}, maxDD {bh_te['maxdd']*100:.0f}%, Sharpe {bh_te['sharpe']:.2f}. "
        f"Single-strategy random-timing p = {p_te:.3f}")
    return dict(rows=rows_out, bh=bh, bh_te=bh_te, sel=sel, p_fam=p_fam, p_te=p_te, ret=ret, cost=cost, split=split, ppy=ppy)


def research(args, out=print):
    rows = data.load("60m")
    if len(rows) < 5000:
        out("not enough data: run `python btc_bot.py fetch` first"); return
    g = data.gaps(rows, "60m")
    out(f"{len(rows)} hourly candles {date(rows[0][0])} .. {date(rows[-1][0])}; {len(g)} gaps ({sum(x[1] for x in g)} missing bars)")
    res = {}
    for factor in (1, 4):
        res[factor] = evaluate(rows, factor, args.delay, args.fee, args.slip, args.shifts, out=out)
    out("\n--- stress test of each timeframe's TRAIN-selected strategy: costs x3, and one extra bar of execution delay ---")
    for factor in (1, 4):
        sel = res[factor]["sel"]["name"]
        for label, kw in (("costs x3", dict(fee=args.fee * 3, slip=args.slip * 3, delay=args.delay)), ("delay +1 bar", dict(fee=args.fee, slip=args.slip, delay=args.delay + 1))):
            quiet = []
            r = evaluate(rows, factor, kw["delay"], kw["fee"], kw["slip"], 50, out=quiet.append)
            m = next(x for x in r["rows"] if x["name"] == sel)
            out(f"{factor}h {sel:34s} {label:13s}: test CAGR {fmt_pct(m['test']['cagr'])}, Sharpe {m['test']['sharpe']:.2f}, maxDD {m['test']['maxdd']*100:.0f}%")
    return res


def cross_asset(args, symbols, fam="ema_trend", params=None, out=print):
    """Apply ONE frozen strategy (chosen from BTC) to other assets it has never seen, 4h bars.
    Compared with random timing at the same exposure (circular-shift test) and with buy & hold."""
    params = params or dict(fast=24, slow=200)
    nm = S.name_of(fam, params)
    cost = args.fee + args.slip
    out(f"\n=== cross-asset test of {nm} on 4h bars (frozen from BTC; fee+slip {cost*100:.2f}% per side) ===")
    out(f"{'symbol':9s} {'bars':>6s} {'from':>10s} {'tr/yr':>6s} {'expo':>5s} {'CAGR':>8s} {'B&H CAGR':>9s} {'maxDD':>6s} {'B&H DD':>7s} {'Sharpe':>6s} {'B&H':>5s} {'null med':>8s} {'null95':>6s} {'p':>6s}")
    rows_, wins = [], 0
    for sym in symbols:
        rr = data.load("4h", sym)
        if len(rr) < 3000:
            out(f"{sym:9s} too little data"); continue
        ts, o, h, l, c = ([r[i] for r in rr] for i in range(5))
        n, ppy = len(c), 8760 / 4
        ret = E.returns(c)
        held = E.held_from_pos(S.positions(fam, params, o, h, l, c), args.delay)
        net, tr = E.simulate(held, ret, cost)
        st = E.stats(net, tr, held, ppy)
        hb = [0] + [1] * (n - 1)
        bh = E.stats(*E.simulate(hb, ret, cost), hb, ppy)
        nt = E.NullTest(ret, ppy, cost)
        obs, null = nt.run([E.segments(held)], k=args.shifts)
        col = sorted(r[0] for r in null)
        p = E.p_single(obs[0], null, 0)
        wins += obs[0] > col[int(len(col) * 0.95)]
        rows_.append((sym, obs[0], col[len(col) // 2], p, st, bh))
        out(f"{sym:9s} {n:6d} {date(ts[0]):>10s} {st['per_year']:6.1f} {st['exposure']*100:4.0f}% {fmt_pct(st['cagr'])} {fmt_pct(bh['cagr'])} {st['maxdd']*100:5.0f}% {bh['maxdd']*100:6.0f}% {obs[0]:6.2f} {bh['sharpe']:5.2f} {col[len(col)//2]:8.2f} {col[int(len(col)*.95)]:6.2f} {p:6.3f}")
    if rows_:
        better_dd = sum(r[4]["maxdd"] < r[5]["maxdd"] for r in rows_)
        beat_bh = sum(r[4]["cagr"] > r[5]["cagr"] for r in rows_)
        pvals = [r[3] for r in rows_]
        out(f"\nsummary over {len(rows_)} assets: Sharpe above the random-timing 95th pct in {wins}; lower max drawdown than buy&hold in {better_dd}; "
            f"higher CAGR than buy&hold in {beat_bh}; median p = {sorted(pvals)[len(pvals)//2]:.3f}; "
            f"mean (Sharpe - random-timing median) = {sum(r[1]-r[2] for r in rows_)/len(rows_):+.2f}")
        out("assets are correlated (crypto moves together) and survivors only, so read this as a sanity check, not as many independent proofs.")
