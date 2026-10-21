#!/usr/bin/env python3
"""quantstats tearsheets and a Monte Carlo drawdown check. Optional: needs `pip install quantstats` (the bot itself does not).

  python report.py backtest                       # 4h EMA 24/200 on BTC since 2019 vs buy and hold  ->  results/report_backtest.html
  python report.py backtest --symbol ETHUSDT      # same frozen rule on another coin (run `btc_bot.py fetch --interval 4h --symbol ETHUSDT` first)
  python report.py paper                          # every paper/lab bot in paper_state/ (or $BTC_BOT_STATE)

The backtest uses btc-bot's own engine (same costs and timing as `research`), so the numbers match research_*.txt.
Paper bots are only reported once they have 30+ days of equity; before that a tearsheet is noise and the script says so.
Monte Carlo reshuffles daily returns 1000 times: how often a year like this one would have hit -35% (the kill switch level)
and how bad the worst 5% of drawdowns get. Reshuffling ignores volatility clustering, so read it as a floor, not a forecast.
"""
import argparse, csv, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data, engine as E, strategies as S
from btc_bot import DEFAULT, parse_strategy

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_PAPER_DAYS = 30
DAYS = 365          # crypto trades every day; quantstats defaults to 252 and would understate CAGR (+35% instead of +55%)


def _qs():
    try:
        import pandas as pd, quantstats as qs
    except ImportError:
        sys.exit("report.py needs quantstats: pip install quantstats")
    return pd, qs


def monte_carlo(daily, pd, sims=1000, bust=0.35, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    r = daily.to_numpy()
    n = min(len(r), 365)
    dds = []
    for _ in range(sims):
        eq = np.cumprod(1 + rng.choice(r, size=n, replace=True))
        dds.append(1 - (eq / np.maximum.accumulate(eq)).min())
    dds = np.array(dds)
    return dict(days=n, p_bust=(dds >= bust).mean(), dd_median=np.median(dds), dd_p95=np.quantile(dds, 0.95))


def write(pd, qs, strat, bench, title, out):
    qs.reports.html(strat, benchmark=bench, title=title, output=out, download_filename=os.path.basename(out),
                    periods_per_year=DAYS)
    mc = monte_carlo(strat, pd)
    m = qs.stats
    print(f"{title}\n  CAGR {m.cagr(strat, periods=DAYS):+.1%}  Sharpe {m.sharpe(strat, periods=DAYS):.2f}  "
          f"Sortino {m.sortino(strat, periods=DAYS):.2f}  "
          f"maxDD {m.max_drawdown(strat):.0%}  (buy&hold maxDD {m.max_drawdown(bench):.0%})\n"
          f"  Monte Carlo, {mc['days']}-day paths: P(drawdown >= 35%) {mc['p_bust']:.0%}, median worst drawdown "
          f"{mc['dd_median']:.0%}, 95th pct {mc['dd_p95']:.0%}\n  -> {os.path.relpath(out)}")


def cmd_backtest(a):
    pd, qs = _qs()
    fam, params = parse_strategy(a.strategy)
    rows = data.load("4h", a.symbol) if a.symbol != "BTCUSDT" else S.resample(data.load("60m"), 4)
    if not rows:
        sys.exit(f"no candles for {a.symbol}: run `python btc_bot.py fetch` (BTC, 1h) or "
                 f"`python btc_bot.py fetch --interval 4h --symbol {a.symbol}` first")
    o, h, l, c = ([r[i] for r in rows] for i in range(1, 5))
    ret, cost = E.returns(c), E.FEE + E.SLIP
    net, _ = E.simulate(E.held_from_pos(S.positions(fam, params, o, h, l, c)), ret, cost)
    bh, _ = E.simulate([1] * len(c), ret, cost)
    idx = pd.to_datetime([r[0] for r in rows], unit="ms")
    daily = lambda x: (1 + pd.Series(x, index=idx)).resample("1D").prod().sub(1).iloc[1:]
    strat, bench = daily(net).rename(S.name_of(fam, params)), daily(bh).rename("buy & hold")
    out = os.path.join(HERE, "results", f"report_backtest_{a.symbol}.html")
    write(pd, qs, strat, bench, f"{a.symbol} {S.name_of(fam, params)} 4h, backtest", out)


def cmd_paper(a):
    pd, qs = _qs()
    state = os.environ.get("BTC_BOT_STATE", os.path.join(HERE, "paper_state"))
    files = sorted(glob.glob(os.path.join(state, "*_equity.csv")))
    if not files:
        sys.exit(f"no *_equity.csv in {state}")
    for f in files:
        name = os.path.basename(f)[:-len("_equity.csv")]
        with open(f) as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["bar_open_utc"])
        eq = df["equity"].astype(float).resample("1D").last().dropna()
        bh = df["buy_hold_equity"].astype(float).resample("1D").last().dropna()
        days = (df.index[-1] - df.index[0]).days
        if days < MIN_PAPER_DAYS:
            print(f"{name}: {days} days of paper data, equity {eq.iloc[-1]:.2f} vs buy&hold {bh.iloc[-1]:.2f}; "
                  f"tearsheet after {MIN_PAPER_DAYS} days")
            continue
        out = os.path.join(state, f"{name}_report.html")
        write(pd, qs, eq.pct_change().dropna().rename(name), bh.pct_change().dropna().rename("buy & hold"),
              f"{name}, paper since {df.index[0]:%Y-%m-%d}", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("backtest"); b.add_argument("--strategy", default=DEFAULT); b.add_argument("--symbol", default="BTCUSDT")
    sp.add_parser("paper")
    a = ap.parse_args()
    {"backtest": cmd_backtest, "paper": cmd_paper}[a.cmd](a)


if __name__ == "__main__":
    main()
