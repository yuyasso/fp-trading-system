from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd


class DataProviderError(Exception):
    pass


class DataProvider(Protocol):
    def get_adjusted_close(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Fetches adjusted close prices for the given tickers.

        Returns a DataFrame with tickers as columns, DatetimeIndex as index,
        and float values representing adjusted close prices.
        """
        ...
