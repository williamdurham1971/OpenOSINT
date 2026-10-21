"""EmaTrend4h plus freqtrade protections. The signal is unchanged; only new entries can be blocked.

Measured on BTC/USDT 2019-2026 (../../README.md, "Protections"): CAGR 52.9% -> 43.0% with no smaller drawdown, because
MaxDrawdown blocks re-entry right when the next uptrend starts. Kept for reference and as an opt-in risk limit; the
recommended strategy is plain `EmaTrend4h`, which stays identical to the research rule (65 trades).

* MaxDrawdown: after closed trades in the last ~2 years lose 35% from their peak, open nothing new for 30 days. This is
  the freqtrade counterpart of btc-bot's `--max-drawdown 0.35`, with two differences: it measures closed trades only,
  and it blocks entries instead of selling. Use `/forceexit all` in Telegram if you also want to be flat.
* CooldownPeriod: after an exit, wait 2 candles (8h) before re-entering, to skip an EMA cross that flips straight back.

StoplossGuard is left out on purpose: this strategy has no stoploss, so it would never fire.
Backtest with `--enable-protections`, otherwise freqtrade ignores them.
"""
from EmaTrend4h import EmaTrend4h


class EmaTrend4hProtected(EmaTrend4h):
    @property
    def protections(self):
        return [
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 6 * 365 * 2,
                "trade_limit": 2,
                "stop_duration_candles": 6 * 30,
                "max_allowed_drawdown": 0.35,
            },
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
        ]
