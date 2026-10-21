# BTC/USDT spot bot for MEXC: research first, paper trading by default

Educational software, not financial advice. It never trades real money unless you pass several explicit flags.

```bash
python btc_bot.py selftest            # 8 offline tests (signing, no look-ahead, bot == backtest, kill switch, live broker)
python btc_bot.py fetch               # 1h candles since 2019 (public data, no key)  ->  data/BTCUSDT_60m.csv
python btc_bot.py research            # the honest test: does textbook TA beat random timing after fees?
python btc_bot.py cross               # the frozen strategy on 10 coins it has never seen
python btc_bot.py paper --wait-for-signal --max-drawdown 0.35     # simulated money, runs forever
python btc_bot.py status
python btc_bot.py patterns            # chart-pattern study (falling wedge etc.): BTC fidelity, cross-coin, confirmation
python btc_bot.py lab                 # several strategies at once with simulated money, incl. day-trading candidates
python btc_bot.py lab-status          # each lab bot's equity vs buy and hold, trades, alerts
python btc_bot.py control --name lab_trend_4h --mode pause    # manual override: auto | pause | flat
python btc_bot.py live-check          # validates API keys with a TEST order; nothing is traded
python report.py backtest             # quantstats tearsheet + Monte Carlo drawdowns  ->  results/report_backtest_BTCUSDT.html
python report.py paper                # the same for every lab bot once it has 30+ days of paper data
```
Standard library only; Python 3.10+. `report.py` is the one exception and needs `pip install quantstats`.
On a python.org build of Python for macOS, run its "Install Certificates.command" once if `fetch` fails with
`CERTIFICATE_VERIFY_FAILED`.

## Tearsheet and Monte Carlo (`report.py`, 2026-10-21)
| 4h EMA 24/200, 2019-2026 | CAGR | Sharpe | Sortino | max DD | B&H max DD | P(1-year DD >= 35%) | worst 5% of 1-year DDs |
|---|---|---|---|---|---|---|---|
| BTCUSDT | +54.9% | 1.23 | 1.95 | 52% | 77% | 25% | 48% or worse |
| ETHUSDT | +54.1% | 1.04 | 1.60 | 63% | 79% | 57% | 64% or worse |

Same numbers as `research` and `cross` (the engine is shared). The Monte Carlo line answers "how likely is the 35% kill
switch to fire in a given year": about 1 year in 4 on BTC. Shuffled daily returns ignore crash clustering, so the real
odds are higher. quantstats assumes 252 trading days a year unless told otherwise; `report.py` passes 365.

## What the research found (MEXC BTCUSDT, 68,380 hourly bars, 2019-01-01 to 2026-10-20, taker 0.05% + 0.02% slippage)
Raw outputs: `results/`. Protocol fixed before looking: 4 textbook families x 6 round parameter sets x {1h, 4h} bars,
long/flat spot, selection only on the first 60% of history, the last 40% looked at once, plus a circular-shift test that
asks how well *random timing with the same exposure* does (this controls for BTC's huge upward drift and for having tried
24 strategies).

* **Day-trading speed does not pay.** The 1h variants that trade 100-450 times a year earned less than the slow ones and less
  than buy and hold (EMA 12/48: +24% a year; Donchian(24): +1%; the fastest, 24-hour momentum, **-30% a year with a 98% max
  drawdown**). RSI dip-buying is invested only 0-11% of the time and earned about nothing (-5% to +8% a year).
* **Slow trend following is the only family that looks decent** (4h EMA/Donchian/momentum with windows of days to weeks:
  Sharpe 1.0-1.3, 1-12 trades a year, max drawdown 41-63% vs 77% for buy and hold). But:
  * versus random timing with the same exposure the best-of-grid is **not significant** (p = 0.065 on 1h, 0.33 on 4h);
  * the strategy chosen on the train period earned **less than buy and hold on the test period** (CAGR +18-22% vs +45%),
    p = 0.21-0.39;
  * results move a lot with one bar of delay on 1h (CAGR 21.6% -> 33.1%), a sign that noise dominates; tripling costs kills it.
* **Cross-asset check** of the frozen rule `ema_trend(24,200)` on 4h (chosen after seeing BTC, so BTC itself is in-sample):
  lower max drawdown than buy and hold on 10 of 10 coins, Sharpe above the random-timing 95th percentile on 4 of 10,
  higher CAGR than buy and hold on 6 of 10, mean Sharpe edge over random timing +0.37. Coins are correlated and are
  survivors, so this is a sanity check, not ten independent proofs.

**Reading:** a slow trend filter behaves like "hold BTC but step aside in long downtrends": a consistent *risk* benefit
(smaller drawdowns, especially 2022: -45% vs -64%), no demonstrated *return* edge over buying and holding, and it lags in
strong bull years (2020, 2023, 2024). It is not a money machine, and in this data faster trading was worse, not better.

## Chart patterns (falling wedge and 10 others): what the data says
`patterns.py` detects falling/rising wedges, ascending/descending/symmetrical triangles, double tops/bottoms, (inverse)
head and shoulders and bull/bear flags from an ATR-scaled zig-zag. A pattern fires only on the CLOSE that breaks it, using
bars up to that close (`selftest` cuts history at every signal bar to prove it, and catches an injected look-ahead bug).
Full output: `results/patterns_2026-10-21.txt`.

* **BTC, last 2 years and full history:** no pattern beats random timing after correcting for the number of patterns tested
  (0 of 18 and 0 of 33 groups at FDR q < 0.10, at both +24h and +72h). The falling wedge on 1h bars (181 signals) did *worse*
  than a random long at +24h. Cells that look great have 8-15 signals and confidence intervals of +/-25 points.
* **10 coins pooled** suggested the falling wedge (+72h): +0.93% over random, 9/10 coins positive. Clustering signals by week
  (coins move together) cut the evidence from p=0.003 to p=0.058. **A pre-registered confirmation on 21 different coins**
  (2019-era majors and collapsed coins such as LUNA, FTT, OMG) **failed**: -0.02% vs random (p=0.52), +0.47% in the first half
  and -0.71% in the second. The discovery set earned +1.44% per trade, the confirmation set +0.02%: survivorship, not the pattern.
* **Trading the bullish patterns on BTC** (enter on the breakout, exit at the swing stop, the measured-move target or 72h) made
  -7% to +4% a year vs +49% for buy and hold, never better than random entries with the same stop/target geometry
  (p 0.10-0.88), and still not with near-zero costs. The patterns did not predict; costs are not the reason.

## The bot
Default strategy `ema_trend:fast=24,slow=200` on **4h** bars: hold BTC while the 24-bar EMA is above the 200-bar EMA, otherwise
sit in USDT. About 8 trades a year, ~50% of time invested. Robust to costs x5 (CAGR 55% -> 48%) and to 3 bars of delay, and its
neighbours (fast 12-48, slow 100-300) all land at Sharpe 1.14-1.40, so it is a plateau, not one lucky setting. **It was picked
with hindsight from the full BTC history**; the only out-of-sample evidence is the cross-asset test above.

* One decision per *closed* bar, a few seconds after the close; the same strategy code as the backtest (`selftest` checks on replayed synthetic
  data that the bot reproduces the backtest's positions and equity to within 0.2%, and that appending future bars never changes a
  past signal).
* Restart-safe: state in `data/<name>_state.json`, trades in `<name>_trades.csv`, equity vs buy and hold in `<name>_equity.csv`.
* `--wait-for-signal`: do not join a trend that is already running; wait for it to end first (recommended: the signal is often
  marginal when you start).
* `--max-drawdown 0.35`: kill switch, flatten and halt when equity falls 35% below its peak. Note the strategy itself had a
  53% drawdown historically, so a tight kill switch will trigger and changes the strategy; choose it as your risk limit.

## Paper lab, manual override, catalyst alerts
`lab` runs bots side by side with simulated money: the 4h trend filter, a fast 1h Donchian(24), all bullish patterns on
1h, the falling wedge alone on 1h, the bullish symmetrical triangle on 1h, and `lab_multi10_4h`: the 4h trend filter on
the 10 cross-asset coins (BTC ETH SOL BNB XRP DOGE ADA LTC LINK TRX), one sleeve of capital/10 per coin, each buy sized
by 60% / the coin's realised volatility over 30 days (capped at 1x, the rest stays in USDT). `lab-status` shows it as one
line (summed equity, how many coins are held); each sleeve keeps its own `lab_multi10_4h_<COIN>_*` files. It exists to
get ~60 trades a year instead of ~8, so the forward test can say something within months. The sleeves do not share cash
(freqtrade's `EmaTrend4hMulti` does), so the two can differ slightly; the coin list is survivors picked in 2026. They evaluate *forward*, on bars none of the research has seen. Each bot joins the
strategy's current state at start-up (e.g. a pattern trade opened in the last 72h). `lab-status` compares each with buy and hold.
`control --name <bot> --mode pause|flat|auto` is the manual override for when you see a catalyst: `pause` keeps the current
position and opens nothing new, `flat` sells and stays out, `auto` resumes. Every bot writes `<name>_alerts.csv` and logs
`ABNORMAL MOVE` when a bar moves more than 4 standard deviations of the last 200 bars: that is usually news, the moment
technical signals are least trustworthy and your judgement matters most.

## Going live (real money): read all of this
**Prefer `freqtrade/` for real money.** It runs the same rule (65 trades, same backtest), with mature order handling and
Telegram control; see `freqtrade/README.md`. The `live` command below stays for completeness.

`live` needs `MEXC_API_KEY`/`MEXC_API_SECRET`, `--max-usdt N`, `--max-drawdown X` and `--yes-i-risk-real-money`.
* Create a **spot-trading-only** key with **withdrawals disabled** and restricted to your IP.
* Run `live-check` first (it calls MEXC's *test order* endpoint, nothing is executed).
* The live adapter has **not been exercised against the real exchange** in this repository (no keys here). Request signing is
  verified against MEXC's documented example, the order-building and the "sell only what the bot bought" logic are unit-tested
  with a fake exchange. Start with an amount you can afford to lose entirely and watch the first trades by hand.
* The bot sells only the BTC it bought itself, never the rest of your account. Fees use the 0.05% taker rate from
  `exchangeInfo`; your own tier or MX discount may differ. Market orders can slip more than assumed in thin moments.
* Stablecoin, exchange, regulatory and API risk are yours. Check that MEXC is available and legal where you live.

## Honest limits
Hindsight selection of the final parameters; a single asset history with one huge bull run; survivor-only coins in the
cross-asset test; the null test controls exposure but not regime; costs are modelled, not measured; 7.8 years contain only a
handful of full trend cycles, so even a "significant" result would rest on few independent episodes.
Notes on the exchange API: klines page only with `startTime` **and** `endTime` and return at most 500 bars; MEXC's docs list two
different signatures for the same input (Examples 1 and 2); the code matches Example 2 and the signing test pins it.
