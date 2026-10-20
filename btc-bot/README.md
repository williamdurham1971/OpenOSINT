# BTC/USDT spot bot for MEXC: research first, paper trading by default

Educational software, not financial advice. It never trades real money unless you pass several explicit flags.

```bash
python btc_bot.py selftest            # 8 offline tests (signing, no look-ahead, bot == backtest, kill switch, live broker)
python btc_bot.py fetch               # 1h candles since 2019 (public data, no key)  ->  data/BTCUSDT_60m.csv
python btc_bot.py research            # the honest test: does textbook TA beat random timing after fees?
python btc_bot.py cross               # the frozen strategy on 10 coins it has never seen
python btc_bot.py paper --wait-for-signal --max-drawdown 0.35     # simulated money, runs forever
python btc_bot.py status
python btc_bot.py live-check          # validates API keys with a TEST order; nothing is traded
```
Standard library only; Python 3.10+.

## What the research found (MEXC BTCUSDT, 68,380 hourly bars, 2019-01-01 to 2026-10-20, taker 0.05% + 0.02% slippage)
Raw outputs: `results/`. Protocol fixed before looking: 4 textbook families x 6 round parameter sets x {1h, 4h} bars,
long/flat spot, selection only on the first 60% of history, the last 40% looked at once, plus a circular-shift test that
asks how well *random timing with the same exposure* does (this controls for BTC's huge upward drift and for having tried
24 strategies).

* **Day-trading speed loses.** Short look-backs (24 bars) trade 100-450 times a year: tsmom(24h) CAGR -30%, max drawdown 98%;
  donchian(24) ~0%. RSI dip-buying barely participates (0-11% exposure) and earns ~0.
* **Slow trend following is the only family that looks decent** (4h EMA/Donchian/momentum with 4-14 day windows: Sharpe
  1.0-1.3, 4-12 trades a year, max drawdown 41-60% vs 77% for buy and hold). But:
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
strong bull years (2020, 2023, 2024). It is not a money machine. Anything that trades several times a day lost money here.

## The bot
Default strategy `ema_trend:fast=24,slow=200` on **4h** bars: hold BTC while the 24-bar EMA is above the 200-bar EMA, otherwise
sit in USDT. About 8 trades a year, ~50% of time invested. Robust to costs x5 (CAGR 55% -> 48%) and to 3 bars of delay, and its
neighbours (fast 12-48, slow 150-300) all land at Sharpe 1.14-1.40, so it is a plateau, not one lucky setting. **It was picked
with hindsight from the full BTC history**; the only out-of-sample evidence is the cross-asset test above.

* One decision per *closed* bar, a few seconds after the close; the same strategy code as the backtest (`selftest` proves the bot
  reproduces the backtest's positions and equity to within 0.2% on replayed data, and that appending future bars never changes a
  past signal).
* Restart-safe: state in `data/<name>_state.json`, trades in `<name>_trades.csv`, equity vs buy and hold in `<name>_equity.csv`.
* `--wait-for-signal`: do not join a trend that is already running; wait for it to end first (recommended: the signal is often
  marginal when you start).
* `--max-drawdown 0.35`: kill switch, flatten and halt when equity falls 35% below its peak. Note the strategy itself had a
  53% drawdown historically, so a tight kill switch will trigger and changes the strategy; choose it as your risk limit.

## Going live (real money): read all of this
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
