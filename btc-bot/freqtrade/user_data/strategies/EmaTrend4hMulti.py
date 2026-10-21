"""EmaTrend4h on several coins at once, each position sized by volatility (use with config.mexc.multi.json).

Why: one coin with ~8 trades a year gives a forward test almost nothing to judge for years. The cross-asset test in
btc-bot/results/ found the same frozen rule cut drawdowns on 10 of 10 coins, so running it on all of them adds
(correlated) evidence and spreads the risk.

Sizing: freqtrade proposes an equal share of the wallet per open slot. That share is scaled by
TARGET_VOL / realised volatility (30 days of 4h returns, annualised), capped at 1x, so a coin that swings twice as hard
gets about half the money. Nothing is levered; the unused part stays in USDT.
"""
import numpy as np

from EmaTrend4h import EmaTrend4h

BARS_PER_YEAR = 6 * 365


class EmaTrend4hMulti(EmaTrend4h):
    TARGET_VOL = 0.60       # annualised; BTC has mostly run at 40-80%
    VOL_BARS = 6 * 30

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        ret = dataframe["close"].pct_change()
        dataframe["ann_vol"] = ret.rolling(self.VOL_BARS).std() * np.sqrt(BARS_PER_YEAR)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return proposed_stake
        vol = df["ann_vol"].iat[-1]
        if not np.isfinite(vol) or vol <= 0:
            return proposed_stake
        stake = proposed_stake * min(1.0, self.TARGET_VOL / vol)
        return max(stake, min_stake or 0.0)
