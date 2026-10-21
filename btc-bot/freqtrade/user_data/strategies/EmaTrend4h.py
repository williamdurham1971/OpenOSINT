"""The btc-bot trend filter as a freqtrade strategy: hold BTC while EMA(24) > EMA(200) on 4h candles, otherwise USDT.

Same rule as `ema_trend:fast=24,slow=200` in btc-bot (see btc-bot/README.md for what the research does and does not show).
No ROI table, no stoploss and no trailing stop: the only exit is the trend turning down, exactly like the backtest.
"""
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class EmaTrend4h(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False

    minimal_roi = {"0": 100.0}      # effectively off: never take profit by ROI
    stoploss = -0.99                # effectively off: the trend exit is the only exit
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    process_only_new_candles = True
    startup_candle_count = 600      # EMA(200) needs a long warm-up to match the research numbers

    fast = 24
    slow = 200

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.fast)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.slow)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["ema_fast"] > dataframe["ema_slow"], "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["ema_fast"] <= dataframe["ema_slow"], "exit_long"] = 1
        return dataframe
