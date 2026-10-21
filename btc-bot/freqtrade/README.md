# btc-bot trend filter on freqtrade (MEXC)

The research and the paper lab live in `btc-bot/`. This folder is for the step after that: running the same rule with
freqtrade's mature order handling and Telegram control. Simulated money (`dry_run: true`) by default.

* `user_data/strategies/EmaTrend4h.py`: hold BTC while EMA(24) > EMA(200) on 4h candles, otherwise USDT. No ROI table, no
  stoploss, no trailing stop: the trend turning down is the only exit, exactly as in the research.
* `config.mexc.json`: MEXC spot, BTC/USDT only, one position, market orders, no keys in the file.

## Verified in this repository (2026-10-21, freqtrade 2026.9, ccxt 4.5.89)
* **Backtest matches btc-bot's own engine:** on the same MEXC 4h data (2019-01-01 to 2026-10-21, 0.05% fee) both find
  **65 trades**; final balance 27,534 vs 28,722 USDT from 1,000 (CAGR 52.9% vs 53.8%; freqtrade only stakes 99% of
  the balance). The drawdown figures differ (44% vs 53%) because the two tools define drawdown differently.
* **Dry run against live MEXC works:** freqtrade loaded the markets, fetched the 600 warm-up candles in 2 calls, found the
  long signal and placed a simulated market buy.
* **MEXC is not officially supported by freqtrade** ("known to the ccxt library, available for the bot, but not officially
  supported"). Two consequences found and handled here:
  1. MEXC returns at most 500 candles per request, while freqtrade assumed 1000 and silently skipped every other block of
     500 (the first download had 501-candle holes all through 2018-2026 and the backtest showed 32 trades instead of 65).
     `"_ft_has_params": {"ohlcv_candle_limit": 500}` in the config fixes it. **Do not remove that line.**
  2. No stoploss on the exchange. This strategy uses none, but if you add one it runs inside the bot, not at MEXC.

## Install (your computer or a small Linux server; Python 3.11+)
```bash
python3 -m venv ~/ftenv && source ~/ftenv/bin/activate
pip install freqtrade
cd btc-bot/freqtrade
freqtrade download-data -c config.mexc.json --userdir user_data --timeframes 4h --timerange 20180901- --pairs BTC/USDT
freqtrade backtesting  -c config.mexc.json --userdir user_data --timerange 20190101- --fee 0.0005   # expect 65 trades
freqtrade trade        -c config.mexc.json --userdir user_data                                     # dry run, simulated money
```

## Telegram control (recommended)
Create a bot with @BotFather, get your chat id, then start freqtrade with environment variables (nothing secret in git):
```bash
export FREQTRADE__TELEGRAM__ENABLED=true
export FREQTRADE__TELEGRAM__TOKEN="<bot token>"
export FREQTRADE__TELEGRAM__CHAT_ID="<your chat id>"
```
`/status`, `/profit`, `/balance`, `/stop`, `/start`, `/forceexit all` then work from your phone. `/stop` plus
`/forceexit all` is the manual override for a catalyst.

## Real money: only after the paper lab says so
The research found no reliable edge beyond "hold BTC but step aside in long downtrends" (see `btc-bot/README.md`). If you still
go live:
1. Create a MEXC API key with **spot trading only, withdrawals disabled, restricted to your server's IP**.
2. Pass it through the environment, never the file:
   `export FREQTRADE__EXCHANGE__KEY=... FREQTRADE__EXCHANGE__SECRET=...`
3. Set `"dry_run": false` and lower the stake: e.g. `"stake_amount": 50` instead of `"unlimited"`.
4. Watch the first trades by hand. Check MEXC is available and legal where you live.
