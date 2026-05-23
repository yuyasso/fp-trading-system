"""
adapters/data/yfinance_adapter.py
----------------------------------
Infrastructure adapter: loads OHLCV daily data from Yahoo Finance via yfinance.

Implements DataSourcePort structurally (no explicit inheritance).
Domain (domain/ports/) must NOT import this module.
"""
from __future__ import annotations

import datetime
from typing import Final

import pandas as pd
import yfinance as yf

_EXPECTED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"Open", "High", "Low", "Close", "Volume"}
)


class YFinanceAdapter:
    """
    Thin infrastructure adapter that wraps yfinance.download and normalises
    the output into the domain's OHLCV contract.

    Structural implementation of DataSourcePort — no explicit base class.
    """

    def load_ohlcv_daily(
        self,
        tickers: list[str],
        start: datetime.date,
        end: datetime.date,
    ) -> pd.DataFrame:
        """
        Download OHLCV daily bars for *tickers* from Yahoo Finance.

        Uses ``auto_adjust=True`` so that ``close`` reflects prices fully
        adjusted for splits and dividends (no separate Adj Close column).

        Contract:
        - Returns DataFrame with MultiIndex ``(date: datetime.date, ticker: str)``,
          sorted by date ASC.
        - Columns: ``[open, high, low, close, volume]``, all ``float64``.
          ``volume`` is always ``float64`` even when all values are present,
          because yfinance may return ``float`` when data has gaps.
        - NaN cells are propagated without modification — no ffill, bfill,
          fillna, or interpolation is applied.
        - Invalid tickers or empty date ranges: yfinance returns an empty
          DataFrame; this adapter propagates that empty result without raising.

        Raises:
            ValueError: If the columns returned by yfinance do not include the
                expected set ``{Open, High, Low, Close, Volume}``. This guards
                against breaking API changes between yfinance versions.
        """
        raw: pd.DataFrame = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
        )

        if raw.empty:
            return pd.DataFrame()

        # --- Guard: expected columns must be present -------------------------
        if isinstance(raw.columns, pd.MultiIndex):
            actual_fields: set[str] = set(raw.columns.get_level_values(0).unique())
        else:
            actual_fields = set(raw.columns)

        missing = _EXPECTED_COLUMNS - actual_fields
        if missing:
            raise ValueError(
                f"yfinance returned columns that do not match the expected OHLCV set. "
                f"Missing columns: {sorted(missing)}. "
                f"Received columns: {sorted(actual_fields)}."
            )

        # --- Normalise to MultiIndex (date, ticker) row index ----------------
        if isinstance(raw.columns, pd.MultiIndex):
            result = _build_from_multilevel_cols(raw)
        else:
            result = _build_from_flat_cols(raw, tickers[0])

        # --- Lowercase, float64, sort ----------------------------------------
        result = result.rename(columns=str.lower)
        result = result.astype("float64")
        result = result.sort_index()

        return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_from_multilevel_cols(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a MultiIndex-column DataFrame into a (date, ticker) row MultiIndex."""
    ticker_labels = raw.columns.get_level_values(-1).unique()
    expected_sorted = sorted(_EXPECTED_COLUMNS)
    frames: list[pd.DataFrame] = []
    for ticker in ticker_labels:
        ticker_df: pd.DataFrame = raw.xs(ticker, level=-1, axis=1)
        ticker_df = ticker_df[expected_sorted]  # drop unexpected, fix order
        dates = pd.Index(
            [ts.date() if hasattr(ts, "date") else ts for ts in ticker_df.index],
            name="date",
        )
        midx = pd.MultiIndex.from_arrays(
            [dates, [ticker] * len(ticker_df)],
            names=["date", "ticker"],
        )
        ticker_df = ticker_df.copy()
        ticker_df.index = midx
        frames.append(ticker_df)
    return pd.concat(frames)


def _build_from_flat_cols(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalise a single-ticker flat DataFrame to the (date, ticker) MultiIndex."""
    expected_sorted = sorted(_EXPECTED_COLUMNS)
    df = raw[expected_sorted].copy()  # drop unexpected columns, fix order
    dates = pd.Index(
        [ts.date() if hasattr(ts, "date") else ts for ts in df.index],
        name="date",
    )
    midx = pd.MultiIndex.from_arrays(
        [dates, [ticker] * len(df)],
        names=["date", "ticker"],
    )
    df.index = midx
    return df
