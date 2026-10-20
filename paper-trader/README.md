# Polymarket 15m BTC paper trader

Measures, with **no real orders and no API keys**, whether a latency/mispricing edge on
Polymarket's "Bitcoin Up or Down" 15-minute markets survives fees, latency and a realistic
settlement rule. Educational/research tool, not financial advice.

```bash
pip install -r requirements.txt
python paper_trader.py selftest
python paper_trader.py measure --minutes 60     # FIRST: how late is Polymarket really?
python paper_trader.py run --mode both          # taker + maker side by side; leave running for days
python paper_trader.py report
python paper_trader.py backtest --days 14       # many past windows: is the model better than the market?
python paper_trader.py summary                  # health check + P&L digest (exit code 2 = alerts)
python paper_trader.py replay                   # evaluate on the live 1 Hz ticks (the proper test, needs hundreds of windows)
```

## What it does each 15-minute window
1. Finds the market `btc-updown-15m-<window start>` via the Gamma API.
2. Records the strike (BTC price at window start) from the price feed.
3. Streams the Up/Down order books from the CLOB websocket.
4. Fair value: `P(up) = Phi(ln(S/K) / sqrt(Var))`, with per-second vol measured live and the
   variance adjusted for the **Chainlink 60s TWAP** settlement rule.
5. If `fair - ask - taker_fee >= --min-edge`, it paper-buys at the book (walking depth),
   after a simulated `--latency-ms` delay. One position per window.
6. After the window it settles from the feed's final-60s average and writes `data/trades.jsonl`;
   `data/ticks.csv` has a 1 Hz log of price, model and both books for offline research.

## `measure`: latency, before you trust any edge
Runs several feeds plus the live Up/Down book for N minutes and reports:
1. delivery delay per feed (receive time minus source timestamp) and CLOB REST round-trip,
2. who moves first between feeds (lagged correlation, e.g. Coinbase vs Chainlink),
3. how late Polymarket's Up price reacts to each feed (lagged correlation of 1s changes),
4. whether `last_trade_price.side` is the taker's side (the maker simulator relies on it).

Resolution is the `--bin` (0.5s) and `--horizon` (1s) setting; sub-second effects are not resolved.
Use the results to set `--latency-ms`.

## Maker mode (`run --mode maker|both`)
One resting BUY limit at a time, posted when `fair - price >= --maker-min-edge` (one tick above
the best bid by default, `--no-maker-improve` joins the queue instead). Fills are credited only when
the tape supports them: it queues behind existing size at its price, a lower-priced trade fills it,
a trade at its price must first consume the queue ahead, queue cancellations are not credited,
and it is cancelled (after latency) when the edge disappears. Maker fee is assumed 0.
`report` shows fill rate, average queue ahead and PnL per mode. Adverse selection shows up as a
good fill rate with poor realised PnL.

## Verified against Polymarket
- **Boundary price** (strike and final) = mean of the Chainlink BTC/USD feed over the 60s before the boundary.
  `python paper_trader.py verify` compares it with Gamma's official `priceToBeat`/`finalPrice` (these appear ~20 min
  after a window closes; the strike of a window equals the final of the previous one). On 3 boundaries from this
  repo's own feed the error was std 0.02 USD vs 1.4-11 USD for the alternatives; on 24 boundaries using Kraken ticks
  it was std 3.7 USD vs 15-31 USD. Windows are settled with Gamma's resolved outcome; our own estimate is kept for comparison.
- **Taker fee** = `C * 0.07 * p * (1-p)` (docs and the market's `feeSchedule`: rate 0.07, exponent 1, takerOnly): 3.5% of
  notional at 50c, 1.4% at 80c. Makers pay nothing; the 20% maker rebate program is ignored (conservative).

## Running unattended
`deploy/paper-trader.service` (systemd, restarts on failure) keeps `run` alive; `deploy/crontab.example` schedules a daily
digest and an hourly check that posts only on alerts. `summary` reports windows logged vs missing, our boundary price vs
the official result, feed/websocket reconnects, P&L per mode with its standard error, and roughly how many fills you need
before the P&L is more than noise. Alerts: no completed window for `--stale-min`, any disagreement with the official
result, too many feed reconnects, unsettled windows. `--webhook` posts plain text, Slack (`{"text"}`) or Discord
(`{"content"}`); nothing leaves the machine unless you pass it. Disk use is a few MB per day (`feed_*.csv`, `ticks.csv`).

## `backtest`: what 1-minute history says (read the caveats)
Uses Gamma (official strike/outcome), the CLOB price history (about one quote per minute) and Coinbase 1-minute candles.
It compares the model's Brier score with the market's and simulates taker trades with the official fee. Three data sets
are printed on purpose: `Q` decides at each quote's own timestamp with the last completed candle (no look-ahead, slightly
unfair to the model), `A-fresh` uses only quotes at most 15 s old, and `A-all` pairs a quote that can be 90 s old with the
latest spot. `A-all` shows large, "significant" profits that are an artifact of stale quotes; never trust a backtest
that does not control for quote age. It cannot say anything about sub-second latency or maker economics.

## `replay`: the proper evaluation, on your own ticks
`run` logs a 1 Hz line per window to `data/ticks.csv` (price, model fair value, best bid/ask of both books). `replay` joins it
with the official outcomes (from `windows.jsonl`, else Gamma, cached) and, per window and time bucket, decides on the first
tick that has both asks and pays the ask one `--latency-s` later (default 1 s), with the official fee. Unlike `backtest`, the
quote and the spot are the same instant, the ask includes the real spread, and the model sees the real Chainlink feed. It
prints the model-vs-market Brier score, claimed vs realised edge per threshold, and how many trades you need before the
mean P&L is better than +/-0.02 per share. It cannot evaluate maker orders (no queue information at 1 Hz) and it only knows
the best ask, not the depth. With under ~100 windows treat every number as noise; the report says so.

## Caveats you must verify
- Feeds can go silent without an error; `run_feed` reconnects after `STALL_S` seconds without data.
- The CLOB websocket sometimes drops us as a "slow consumer" (about 2 per 3 windows here); books are rebuilt from the
  next snapshot and queue positions are only read from a real book. Trades during the gap are not credited.
- Maker fills depend on the simulated queue model; one live fill came right after a reconnect, so treat very
  small samples with suspicion.
- Check that Polymarket is legal and available to you in your jurisdiction.
- A positive paper result is necessary, not sufficient: real fills, queue position and competitors are worse than any
  simulation, and a few dozen windows say nothing about edge.
