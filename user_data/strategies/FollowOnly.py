"""A strategy that never decides anything: every trade comes from /forceenter and /forceexit.

The stop and ROI are set out of reach so that exits follow 2.0 alone. Freqtrade still closes a
trade at its liquidation price, which at 15x sits past 2.0's stops.
"""

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class FollowOnly(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True
    stoploss = -0.99
    minimal_roi = {"0": 100}
    trailing_stop = False
    use_exit_signal = False
    process_only_new_candles = True
    startup_candle_count = 0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe
