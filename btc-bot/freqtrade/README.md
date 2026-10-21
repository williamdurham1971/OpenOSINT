# btc-bot trend filter on freqtrade (MEXC)

The research and the paper lab live in `btc-bot/`. This folder is for the step after that: running the same rule with
freqtrade's mature order handling and Telegram control. Simulated money (`dry_run: true`) by default.

* `user_data/strategies/EmaTrend4h.py`: hold BTC while EMA(24) > EMA(200) on 4h candles, otherwise USDT. No ROI table, no
  stoploss, no trailing stop: the trend turning down is the only exit, exactly as in the research.
* `config.mexc.json`: MEXC spot, BTC/USDT only, one position, market orders, no keys in the file.
* `user_data/strategies/EmaTrend4hMulti.py` + `config.mexc.multi.json`: the same rule on 10 coins at once, each position
  sized by volatility. See "Several coins" below.
* `user_data/strategies/EmaTrend4hProtected.py`: the same rule plus freqtrade protections. Measured below; **not
  recommended**.

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
  2. No stoploss on the exchange. This strategy uses none, but if you add one it runs inside the bot, not at MEXC: if
     the bot or its server is down, nothing protects the position.

## Bias checks (2026-10-21, freqtrade 2026.9)
```bash
freqtrade lookahead-analysis -c config.mexc.json --userdir user_data --strategy EmaTrend4h --timerange 20190101- \
  --fee 0.0005 --minimum-trade-amount 10 --targeted-trade-amount 40
freqtrade recursive-analysis -c config.mexc.json --userdir user_data --strategy EmaTrend4h --timerange 20240101-20261020 \
  --startup-candle 200 300 400 600 1000
```
* **lookahead-analysis:** no bias in `EmaTrend4h` or `EmaTrend4hMulti` (40 of 40 signals unchanged when future candles
  are cut off). This complements btc-bot's own `selftest`, which checks btc-bot's engine, not the freqtrade strategy.
* **recursive-analysis:** with 600 warm-up candles EMA(200) is within 0.003% of its long-run value (1.3% with only
  200, 0.03% with 400), so `startup_candle_count = 600` is enough. The volatility column in the multi strategy does not
  depend on warm-up at all. Do not lower 600.

## Protections: measured, not recommended (MEXC 4h BTC/USDT 2019-01-01 to 2026-10-21, fee 0.05%)
| | trades | CAGR | Sharpe (daily) | max underwater |
|---|---|---|---|---|
| `EmaTrend4h` (no protections) | 65 | 52.9% | 1.20 | 44.0% |
| + `MaxDrawdown` 35% / 2 years, lock 30 days | 60 | 42.4% | 1.05 | 46.2% |
| + `CooldownPeriod` 2 candles | 64 | 53.5% | 1.21 | 44.0% |
| `EmaTrend4hProtected` (both) | 59 | 43.0% | 1.06 | 46.2% |

`MaxDrawdown` locks new entries right after the big losses, which is when the next uptrend starts: it cost 10 points of
CAGR and did not reduce the drawdown. `CooldownPeriod` changes one trade. Freqtrade protections also only block entries
(they never sell) and measure closed trades only, so they are not a kill switch. For a hard stop use Telegram
(`/stop` + `/forceexit all`) or btc-bot's `--max-drawdown`, and size the account so a 50% drawdown is survivable.
`StoplossGuard` is not included: the strategy has no stoploss, so it would never fire.
Backtest them with `--enable-protections`; without that flag freqtrade ignores them.

## Several coins, sized by volatility
`config.mexc.multi.json` runs the same rule on the 10 coins of btc-bot's cross-asset test (BTC ETH SOL BNB XRP DOGE ADA
LTC LINK TRX), up to 10 positions. `EmaTrend4hMulti` scales each position by 60% / the coin's realised volatility
(30 days, annualised, capped at 1x), so wild coins get less money; the rest stays in USDT.

| 10 coins, 2019-01-01 to 2026-10-21 | trades | CAGR | Sharpe (daily) | max underwater | wallet drawdown |
|---|---|---|---|---|---|
| equal stakes (`EmaTrend4h`) | 623 | 68.1% | 1.31 | 55.6% | 42.5% |
| volatility-sized (`EmaTrend4hMulti`) | 623 | 62.3% | 1.40 | 45.1% | 34.3% |

Same signals, smaller swings: about 6 points of CAGR traded for 8-10 points less drawdown. **Read with care:** the
coin list was picked in 2026 from coins that survived (no LUNA, FTT), and the rule itself was chosen on BTC, so this is
the optimistic case. The main reason to run it is the forward test: ~60 trades a year across coins instead of ~8 on BTC
alone, so the paper run says something within months, not years.
```bash
freqtrade download-data -c config.mexc.multi.json --userdir user_data --timeframes 4h --timerange 20180901-
freqtrade backtesting  -c config.mexc.multi.json --userdir user_data --strategy EmaTrend4hMulti --timerange 20190101- --fee 0.0005
freqtrade trade        -c config.mexc.multi.json --userdir user_data --strategy EmaTrend4hMulti                  # dry run
```

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
`/forceexit all` is the manual override for a catalyst. Turn it on before the first dry run, not after: with ~8 trades a
year a silent bot looks the same as a dead one, and Telegram is how you notice. The config keeps `"enabled": false` so
that no token is ever written to git.

## Real money: only after the paper lab says so
The research found no reliable edge beyond "hold BTC but step aside in long downtrends" (see `btc-bot/README.md`). If you still
go live, go live **here**, not with `btc_bot.py live`: btc-bot's own live adapter has never placed an order on the real
exchange, while freqtrade's order handling (partial fills, timeouts, restarts, Telegram control) is used by many people.
Keep btc-bot for research and the paper lab.
1. Create a MEXC API key with **spot trading only, withdrawals disabled, restricted to your server's IP**.
2. Pass it through the environment, never the file:
   `export FREQTRADE__EXCHANGE__KEY=... FREQTRADE__EXCHANGE__SECRET=...`
3. Set `"dry_run": false` and lower the stake: e.g. `"stake_amount": 50` instead of `"unlimited"`.
4. Watch the first trades by hand. Check MEXC is available and legal where you live.
