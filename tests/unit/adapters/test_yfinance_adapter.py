"""
Tests for adapters/data/yfinance_adapter.py

All tests mock yfinance.download — zero real network calls.
"""
from __future__ import annotations

import datetime
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.ports.data_source import DataSourcePort  # noqa: F401 — triggers module coverage

# ---------------------------------------------------------------------------
# Mock-data helpers
# ---------------------------------------------------------------------------


def _make_single_ticker_df(
    dates: list[str] | None = None,
    with_nan: bool = False,
) -> pd.DataFrame:
    """Simulate yfinance.download output for a single ticker (flat columns)."""
    if dates is None:
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    idx = pd.to_datetime(dates)
    n = len(dates)
    opens = [150.0, 152.0, 154.0][:n]
    highs = [155.0, 157.0, 159.0][:n]
    lows = [148.0, 150.0, 152.0][:n]
    closes = [153.0, 156.0, 158.0][:n]
    vols = [1_000_000.0, 1_200_000.0, 900_000.0][:n]
    if with_nan:
        closes[1] = float("nan")
        vols[2] = float("nan")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def _make_multi_ticker_df(
    dates: list[str] | None = None,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Simulate yfinance.download output for multiple tickers (MultiIndex columns)."""
    if dates is None:
        dates = ["2024-01-02", "2024-01-03"]
    if tickers is None:
        tickers = ["AAPL", "MSFT"]
    idx = pd.to_datetime(dates)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    cols = pd.MultiIndex.from_product([fields, tickers], names=["Price", "Ticker"])
    rng = np.random.default_rng(42)
    data = rng.uniform(100, 200, size=(len(dates), len(fields) * len(tickers)))
    return pd.DataFrame(data, index=idx, columns=cols)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestYFinanceAdapter:
    def test_single_ticker_columns_and_shape(self) -> None:
        with patch("yfinance.download", return_value=_make_single_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_multi_ticker_multiindex_structure(self) -> None:
        with patch("yfinance.download", return_value=_make_multi_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL", "MSFT"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert isinstance(df.index, pd.MultiIndex)
        assert df.index.nlevels == 2

    def test_multiindex_order_is_date_first(self) -> None:
        with patch("yfinance.download", return_value=_make_multi_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL", "MSFT"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert df.index.names == ["date", "ticker"]

    def test_invalid_ticker_returns_empty_dataframe(self) -> None:
        with patch("yfinance.download", return_value=pd.DataFrame()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["INVALID"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert df.empty

    def test_empty_date_range_returns_empty_dataframe(self) -> None:
        with patch("yfinance.download", return_value=pd.DataFrame()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 5), date(2024, 1, 5)
            )
        assert df.empty

    def test_close_is_adjusted_auto_adjust_true(self) -> None:
        with patch(
            "yfinance.download", return_value=_make_single_ticker_df()
        ) as mock_dl:
            YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        mock_dl.assert_called_once_with(
            ["AAPL"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 5),
            auto_adjust=True,
            progress=False,
        )

    def test_columns_are_lowercase(self) -> None:
        with patch("yfinance.download", return_value=_make_single_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        for col in df.columns:
            assert col == col.lower(), f"Column '{col}' is not lowercase"

    def test_volume_is_float64(self) -> None:
        with patch("yfinance.download", return_value=_make_single_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert df["volume"].dtype == np.float64

    def test_missing_data_propagates_nan(self) -> None:
        with patch(
            "yfinance.download", return_value=_make_single_ticker_df(with_nan=True)
        ):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert df["close"].isna().any(), "Expected NaN in 'close' but none found"
        assert df["volume"].isna().any(), "Expected NaN in 'volume' but none found"

    def test_output_is_sorted_by_date(self) -> None:
        # Provide dates in reverse order; result must be ASC
        reversed_df = _make_single_ticker_df(
            dates=["2024-01-04", "2024-01-03", "2024-01-02"]
        )
        with patch("yfinance.download", return_value=reversed_df):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        date_values = [idx[0] for idx in df.index]
        assert date_values == sorted(date_values)

    def test_no_extra_columns(self) -> None:
        # Mock includes an extra "Dividends" column that yfinance might add
        mock_df = _make_single_ticker_df()
        mock_df["Dividends"] = 0.0
        with patch("yfinance.download", return_value=mock_df):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_guard_raises_on_unexpected_yfinance_columns(self) -> None:
        # Simulate a yfinance API change that renames OHLCV columns
        bad_df = pd.DataFrame(
            {
                "Adj_Open": [150.0],
                "Adj_High": [155.0],
                "Adj_Low": [148.0],
                "Adj_Close": [153.0],
                "Adj_Volume": [1_000_000.0],
            },
            index=pd.to_datetime(["2024-01-02"]),
        )
        with patch("yfinance.download", return_value=bad_df):
            with pytest.raises(ValueError):
                YFinanceAdapter().load_ohlcv_daily(
                    ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
                )

    def test_guard_error_message_mentions_missing_columns(self) -> None:
        # Minimal bad response — only one unexpected column
        bad_df = pd.DataFrame(
            {"Adj_Close": [153.0]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        with patch("yfinance.download", return_value=bad_df):
            with pytest.raises(ValueError, match="[Mm]issing"):
                YFinanceAdapter().load_ohlcv_daily(
                    ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
                )

    def test_single_ticker_normalized_to_multiindex(self) -> None:
        # yfinance returns flat columns for a single ticker;
        # the adapter must wrap it in the same (date, ticker) MultiIndex contract.
        with patch("yfinance.download", return_value=_make_single_ticker_df()):
            df = YFinanceAdapter().load_ohlcv_daily(
                ["AAPL"], date(2024, 1, 1), date(2024, 1, 5)
            )
        assert isinstance(df.index, pd.MultiIndex)
        assert df.index.names == ["date", "ticker"]
        assert "AAPL" in df.index.get_level_values("ticker")
