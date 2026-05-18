from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf

from trading.ports.data_provider import DataProviderError


class YahooFinanceProvider:
    """
    Fetches adjusted close prices from Yahoo Finance via yfinance.

    Implements the DataProvider port. Contains no business logic:
    all computation of signals, volatility, or weights belongs in the
    domain layer, not here.
    """

    def get_adjusted_close(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Downloads adjusted close prices for the given tickers.

        Uses auto_adjust=True so the returned 'Close' column reflects
        split- and dividend-adjusted prices.

        Raises:
            DataProviderError: If no data is returned or a ticker has all NaN.
        """
        raw: pd.DataFrame = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )

        if raw.empty:
            raise DataProviderError(
                f"No data returned from Yahoo Finance for: {tickers}"
            )

        if "Close" not in raw.columns:
            raise DataProviderError(
                "Yahoo Finance response is missing the 'Close' column"
            )

        close_data = raw["Close"]
        if isinstance(close_data, pd.Series):
            df: pd.DataFrame = close_data.to_frame(name=tickers[0])
        else:
            df = close_data

        if df.empty or bool(df.isna().values.all()):
            raise DataProviderError(
                f"All values are NaN for tickers: {tickers}"
            )

        for ticker in tickers:
            if ticker in df.columns and bool(df[ticker].isna().all()):
                raise DataProviderError(
                    f"No data returned for ticker: {ticker}"
                )

        return df
