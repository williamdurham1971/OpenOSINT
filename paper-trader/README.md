# Polymarket 15m BTC paper trader

Measures, with **no real orders and no API keys**, whether a latency/mispricing edge on
Polymarket's "Bitcoin Up or Down" 15-minute markets survives fees, latency and a realistic
settlement rule. Educational/research tool, not financial advice.

```bash
pip install -r requirements.txt
python paper_trader.py selftest
python paper_trader.py run --feed chainlink     # leave running for days
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

## Caveats you must verify
- Settlement is Chainlink BTC/USD 60s TWAP (read from the market description), not Binance.
  `--feed chainlink` uses Polymarket's own relay of that stream; `binance`/`coinbase` are proxies.
- The fee formula (`--fee-rate 0.25 --fee-exp 2`, ~1.56% peak) is from third-party sources.
  Check Polymarket's current fee docs. Fees and rules change.
- The strike is approximated by the first feed sample at the window start.
- Check that Polymarket is legal and available to you in your jurisdiction.
- A positive paper result is necessary, not sufficient: real fills, queue position and
  competitors are worse than any simulation.
