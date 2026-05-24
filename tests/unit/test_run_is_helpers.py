"""
tests/unit/test_run_is_helpers.py
----------------------------------
Unit tests for the testable helper functions in scripts/run_is.py.

All tests are pure or use mocks — zero real network calls.
The if __name__ == "__main__" block is NOT tested here (requires network).
"""
from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from run_is import (
    _STATISTICAL_WARNING,
    _make_metrics,
    compute_asset_attribution,
    compute_subperiod_sharpes,
)

from trading.domain.metrics.equity_metrics import Frequency, PerformanceReport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SUB_PERIODS = [
    ("2005-01-01", "2008-12-31"),
    ("2009-01-01", "2013-12-31"),
    ("2014-01-01", "2018-12-31"),
    ("2019-01-01", "2021-12-31"),
]

_EXPECTED_KEYS = {"2005-08", "2009-13", "2014-18", "2019-21"}


def _make_equity(
    start: str = "2005-01-03",
    periods: int = 4500,
    seed: int = 42,
) -> pd.Series:  # type: ignore[type-arg]
    """Synthetic daily-return series with datetime.date index."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq="B")
    returns = pd.Series(rng.normal(0.0003, 0.01, periods), index=idx)
    returns.index = pd.Index([d.date() for d in returns.index], name="date")
    return returns


def _make_mock_ohlcv(
    tickers: list[str],
    n: int = 300,
    opens: np.ndarray | None = None,
) -> pd.DataFrame:
    """Build a MultiIndex (date, ticker) OHLCV DataFrame like the adapter returns."""
    dates = [d.date() for d in pd.date_range("2010-01-04", periods=n, freq="B")]
    rng = np.random.default_rng(0)
    closes = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.008, n))
    if opens is None:
        opens = closes * (1 + rng.normal(0, 0.001, n))

    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        midx = pd.MultiIndex.from_arrays(
            [dates, [ticker] * n], names=["date", "ticker"]
        )
        df = pd.DataFrame(
            {
                "open": opens,
                "high": np.maximum(opens, closes) * 1.002,
                "low": np.minimum(opens, closes) * 0.998,
                "close": closes,
                "volume": np.ones(n) * 1_000_000.0,
            },
            index=midx,
        )
        frames.append(df)
    return pd.concat(frames).sort_index()


# ---------------------------------------------------------------------------
# compute_subperiod_sharpes
# ---------------------------------------------------------------------------


def test_subperiod_sharpe_correct_slice() -> None:
    """Sharpe for a sub-period slice matches manual calculation on that slice."""
    equity = _make_equity()

    result = compute_subperiod_sharpes(equity, [("2009-01-01", "2013-12-31")])

    start_d = date(2009, 1, 1)
    end_d = date(2013, 12, 31)
    mask = (equity.index >= start_d) & (equity.index <= end_d)
    slice_ = equity.loc[mask]
    # Manual Sharpe: mean/std (ddof=1) * sqrt(252)
    expected = float(slice_.mean() / slice_.std() * np.sqrt(252))

    assert abs(result["2009-13"] - expected) < 1e-10


def test_subperiod_sharpe_all_four_keys_present() -> None:
    """Result dict has exactly the four expected keys."""
    equity = _make_equity()
    result = compute_subperiod_sharpes(equity, _SUB_PERIODS)
    assert set(result.keys()) == _EXPECTED_KEYS


def test_subperiod_sharpe_empty_slice_returns_nan() -> None:
    """Empty slice (no overlap with equity index) yields float('nan'), not exception."""
    equity = _make_equity(start="2015-01-02", periods=100)
    # Request a period with no overlap
    result = compute_subperiod_sharpes(equity, [("2005-01-01", "2005-12-31")])
    assert math.isnan(result["2005-05"])


# ---------------------------------------------------------------------------
# Warmup exclusion arithmetic
# ---------------------------------------------------------------------------


def test_warmup_exclusion_correct_length() -> None:
    """iloc[lookback_months * 21:] trims exactly lookback_months*21 rows."""
    equity = pd.Series(np.zeros(500))
    lookback_months = 12
    trimmed = equity.iloc[lookback_months * 21 :]
    assert len(trimmed) == 500 - 252


# ---------------------------------------------------------------------------
# compute_asset_attribution
# ---------------------------------------------------------------------------


@patch("run_is.YFinanceAdapter")
def test_asset_attribution_tickers_as_columns(mock_cls: MagicMock) -> None:
    """Returned DataFrame has columns == tickers passed in."""
    tickers = ["SPY", "TLT", "GLD"]
    mock_cls.return_value.load_ohlcv_daily.return_value = _make_mock_ohlcv(tickers)

    result_df, _ = compute_asset_attribution(
        tickers,
        date(2010, 1, 1),
        date(2012, 12, 31),
        lookback_months=6,
        target_vol=0.10,
    )

    assert set(result_df.columns) == set(tickers)


@patch("run_is.YFinanceAdapter")
def test_asset_attribution_no_fillna(mock_cls: MagicMock) -> None:
    """NaN in open prices propagates into attribution — no fillna applied."""
    tickers = ["SPY"]
    n = 300
    rng = np.random.default_rng(1)
    closes = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.008, n))
    opens = closes.copy()
    opens[100] = np.nan  # inject NaN at position 100

    mock_cls.return_value.load_ohlcv_daily.return_value = _make_mock_ohlcv(
        tickers, n=n, opens=opens
    )

    result_df, _ = compute_asset_attribution(
        tickers,
        date(2010, 1, 1),
        date(2012, 12, 31),
        lookback_months=6,
        target_vol=0.10,
    )

    # NaN in open propagates through entry_price → daily_returns → attribution
    assert result_df["SPY"].isna().any()


# ---------------------------------------------------------------------------
# _make_metrics / statistical_warning
# ---------------------------------------------------------------------------


def test_json_contains_statistical_warning() -> None:
    """_make_metrics output contains a non-empty 'statistical_warning' key."""
    report = PerformanceReport(
        sharpe=1.0,
        sortino=1.2,
        calmar=0.8,
        max_drawdown=-0.10,
        max_drawdown_duration=120,
        annualized_return=0.08,
        n_periods=4000,
        frequency=Frequency.DAILY,
    )
    metrics = _make_metrics(
        report,
        n_rebalances=192,
        warmup_days=252,
        subperiod_sharpes={
            "2005-08": 0.9, "2009-13": 1.1, "2014-18": 0.7, "2019-21": 1.3
        },
        asset_attribution={"SPY": 0.0003, "TLT": 0.0002, "GLD": 0.0001},
    )

    assert "statistical_warning" in metrics
    warning = metrics["statistical_warning"]
    assert isinstance(warning, str)
    assert len(warning) > 0


def test_statistical_warning_constant_non_empty() -> None:
    """Module-level _STATISTICAL_WARNING is a non-empty string."""
    assert isinstance(_STATISTICAL_WARNING, str)
    assert len(_STATISTICAL_WARNING) > 0
