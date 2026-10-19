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

## Caveats you must verify
- Settlement is Chainlink BTC/USD 60s TWAP (read from the market description), not Binance.
  `--feed chainlink` uses Polymarket's own relay of that stream; `binance`/`coinbase` are proxies.
- The fee formula (`--fee-rate 0.25 --fee-exp 2`, ~1.56% peak) is from third-party sources.
  Check Polymarket's current fee docs. Fees and rules change.
- The strike is approximated by the first feed sample at the window start.
- Check that Polymarket is legal and available to you in your jurisdiction.
- The settlement proxy (mean of the feed over the last 60s) is unverified against Polymarket's
  actual resolutions; compare `outcome` in trades.jsonl with the resolved market before trusting PnL.
- A positive paper result is necessary, not sufficient: real fills, queue position and
  competitors are worse than any simulation.
