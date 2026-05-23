"""
tests/unit/backtest/test_runner.py
------------------------------------
Unit tests for src/trading/backtest/runner.py.

All tests mock YFinanceAdapter.load_ohlcv_daily — zero real network calls.
"""
from __future__ import annotations

import datetime
import logging
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading.backtest.runner import (
    _EWMA_LAMBDA,
    _OOS_START,
    _compute_monthly_signal,
    run_backtest,
)
from trading.domain.metrics.equity_metrics import Frequency, PerformanceReport

# ---------------------------------------------------------------------------
# Test-data helpers
# ---------------------------------------------------------------------------


def _make_mock_ohlcv(
    tickers: list[str],
    closes: np.ndarray,
    opens: np.ndarray,
) -> pd.DataFrame:
    """
    Build a (date, ticker) MultiIndex DataFrame mimicking YFinanceAdapter output.

    Parameters
    ----------
    tickers : list of str
        Ticker symbols (same prices applied to each).
    closes : np.ndarray, shape (n,)
        Adjusted close prices.
    opens : np.ndarray, shape (n,)
        Open prices (may differ from closes to test leakage).
    """
    n = len(closes)
    dates = [d.date() for d in pd.date_range("2015-01-02", periods=n, freq="B")]

    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        highs = np.maximum(opens, closes) * 1.002
        lows = np.minimum(opens, closes) * 0.998
        volumes = np.ones(n) * 1_000_000.0
        midx = pd.MultiIndex.from_arrays(
            [dates, [ticker] * n],
            names=["date", "ticker"],
        )
        frames.append(
            pd.DataFrame(
                {
                    "open": opens.astype(float),
                    "high": highs,
                    "low": lows,
                    "close": closes.astype(float),
                    "volume": volumes,
                },
                index=midx,
            )
        )
    return pd.concat(frames).sort_index()


def _default_mock(n: int = 500, tickers: list[str] | None = None) -> pd.DataFrame:
    """Standard uptrending mock data: closes linearly 100→200, opens = closes + 1."""
    if tickers is None:
        tickers = ["SPY"]
    closes = np.linspace(100.0, 200.0, n)
    opens = closes + 1.0
    return _make_mock_ohlcv(tickers, closes, opens)


# Shared start/end for IS period (well within IS)
_IS_START = date(2015, 1, 1)
_IS_END = date(2021, 6, 30)


# ---------------------------------------------------------------------------
# Helper: run backtest with a given mock DataFrame
# ---------------------------------------------------------------------------


def _run_with_mock(
    mock_data: pd.DataFrame,
    tickers: list[str] | None = None,
    start: date = _IS_START,
    end: date = _IS_END,
    lookback_months: int = 12,
    target_vol: float = 0.10,
) -> tuple[pd.Series, PerformanceReport]:
    if tickers is None:
        tickers = list(mock_data.index.get_level_values("ticker").unique())
    with patch("trading.backtest.runner.YFinanceAdapter") as MockCls:
        MockCls.return_value.load_ohlcv_daily.return_value = mock_data
        return run_backtest(tickers, start, end, lookback_months, target_vol)


# ---------------------------------------------------------------------------
# Tests: IS/OOS boundary
# ---------------------------------------------------------------------------


def test_oos_boundary_clips_end_date() -> None:
    """If end > OOS start, the adapter must be called with end = 2021-12-31."""
    n = 500
    mock_data = _default_mock(n)
    with patch("trading.backtest.runner.YFinanceAdapter") as MockCls:
        instance = MockCls.return_value
        instance.load_ohlcv_daily.return_value = mock_data
        run_backtest(["SPY"], date(2015, 1, 1), date(2025, 1, 1))

    _, call_kwargs = instance.load_ohlcv_daily.call_args
    actual_end: date = instance.load_ohlcv_daily.call_args[0][2]
    assert actual_end == date(2021, 12, 31), (
        f"end should be clamped to 2021-12-31, got {actual_end}"
    )


def test_start_in_oos_raises() -> None:
    """start >= 2022-01-01 must raise ValueError before calling the adapter."""
    with patch("trading.backtest.runner.YFinanceAdapter") as MockCls:
        with pytest.raises(ValueError, match="OOS"):
            run_backtest(["SPY"], date(2022, 1, 1), date(2022, 12, 31))
    MockCls.return_value.load_ohlcv_daily.assert_not_called()


def test_start_in_oos_raises_after_boundary() -> None:
    """start strictly after 2022-01-01 also raises."""
    with patch("trading.backtest.runner.YFinanceAdapter"):
        with pytest.raises(ValueError):
            run_backtest(["SPY"], date(2023, 6, 1), date(2023, 12, 31))


# ---------------------------------------------------------------------------
# Tests: return type and structure
# ---------------------------------------------------------------------------


def test_returns_equity_curve_and_report() -> None:
    """run_backtest must return (pd.Series, PerformanceReport)."""
    equity, report = _run_with_mock(_default_mock())
    assert isinstance(equity, pd.Series)
    assert isinstance(report, PerformanceReport)


def test_equity_curve_index_is_date() -> None:
    """Equity curve index must contain datetime.date objects, not Timestamp."""
    equity, _ = _run_with_mock(_default_mock())
    non_nan = equity.dropna()
    assert len(non_nan) > 0
    for idx_val in non_nan.index[:5]:
        assert isinstance(idx_val, datetime.date), (
            f"Expected datetime.date, got {type(idx_val)}"
        )
        # Ensure it's not a Timestamp (subclass of date in some pandas versions)
        assert not isinstance(idx_val, pd.Timestamp)


def test_equity_curve_name_is_tsmom_is() -> None:
    """Equity curve name must be 'tsmom_is'."""
    equity, _ = _run_with_mock(_default_mock())
    assert equity.name == "tsmom_is"


# ---------------------------------------------------------------------------
# Tests: no data leakage
# ---------------------------------------------------------------------------


def test_no_data_leakage_entry_at_open_next_day() -> None:
    """
    Critical: portfolio returns must use open[t+1] as entry price, not close[t].

    Setup:
    - Close linearly increases 100→200 (strong momentum, signal=1 after lookback).
    - Open = close + BIG_GAP (additive, making the two formulas give different results).
    - target_vol=100.0 forces vol_weight=1.0 (daily vol tiny vs target).

    With vol_weight=1.0 and signal=1.0, the portfolio return at index i equals:
        open[i+2] / open[i+1] - 1   (correct: entry at next day's open)

    If the runner incorrectly used close[t] as entry:
        close[i+1] / close[i] - 1   (which differs because of BIG_GAP)
    """
    n = 500
    BIG_GAP = 100.0
    closes = np.linspace(100.0, 200.0, n)
    opens = closes + BIG_GAP

    mock_data = _make_mock_ohlcv(["SPY"], closes, opens)

    with patch("trading.backtest.runner.YFinanceAdapter") as MockCls:
        MockCls.return_value.load_ohlcv_daily.return_value = mock_data
        equity, _ = run_backtest(
            ["SPY"], _IS_START, _IS_END, lookback_months=12, target_vol=100.0
        )

    # Index 380: well past lookback (252) and before the last-2 NaN tail
    CHECK_IDX = 380
    actual = equity.iloc[CHECK_IDX]

    assert not np.isnan(actual), f"Expected non-NaN return at index {CHECK_IDX}"

    # Correct formula: open[CHECK_IDX+2] / open[CHECK_IDX+1] - 1
    expected_open_based = opens[CHECK_IDX + 2] / opens[CHECK_IDX + 1] - 1.0
    # Wrong formula: close[CHECK_IDX+1] / close[CHECK_IDX] - 1
    expected_close_based = closes[CHECK_IDX + 1] / closes[CHECK_IDX] - 1.0

    # The two formulas give noticeably different results due to BIG_GAP
    assert abs(expected_open_based - expected_close_based) > 1e-4, (
        "Test is misconfigured: the two formulas should produce different results"
    )

    # Actual return must match open-based, not close-based
    assert abs(actual - expected_open_based) < abs(actual - expected_close_based), (
        f"Return {actual:.8f} should be closer to open-based formula "
        f"({expected_open_based:.8f}) than to close-based "
        f"({expected_close_based:.8f}). "
        "Possible leakage: check entry_price = open_.shift(-1)."
    )


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------


def test_ewma_lambda_is_094() -> None:
    """_EWMA_LAMBDA must be exactly 0.94 (RiskMetrics standard)."""
    assert _EWMA_LAMBDA == 0.94


def test_oos_constant_is_2022_01_01() -> None:
    """_OOS_START must be date(2022, 1, 1)."""
    assert _OOS_START == date(2022, 1, 1)


# ---------------------------------------------------------------------------
# Tests: long-only constraint
# ---------------------------------------------------------------------------


def test_signal_is_long_only() -> None:
    """
    _compute_monthly_signal must return only non-negative values.
    Tests with data that includes periods of negative momentum.
    """
    n = 700
    dates = pd.date_range("2015-01-02", periods=n, freq="B")
    # First half: downtrend (negative momentum after lookback)
    # Second half: uptrend
    prices_arr = np.concatenate(
        [np.linspace(200.0, 100.0, n // 2), np.linspace(100.0, 200.0, n - n // 2)]
    )
    close = pd.DataFrame({"SPY": prices_arr, "TLT": prices_arr * 0.8}, index=dates)

    position = _compute_monthly_signal(close, lookback_days=252)
    non_nan = position.dropna()

    assert (non_nan >= 0.0).all(axis=None), (
        "Signal must be long-only (>= 0). Short positions detected."
    )


# ---------------------------------------------------------------------------
# Tests: monthly rebalancing
# ---------------------------------------------------------------------------


def test_monthly_rebalancing() -> None:
    """
    _compute_monthly_signal must produce position values that only change
    at month-start boundaries, never intra-month.
    """
    n = 700
    dates = pd.date_range("2015-01-02", periods=n, freq="B")
    # Monotonically increasing → always positive momentum after lookback
    close = pd.DataFrame(
        {"SPY": np.linspace(100.0, 200.0, n), "TLT": np.linspace(80.0, 160.0, n)},
        index=dates,
    )

    position = _compute_monthly_signal(close, lookback_days=252)

    # Positions after warmup: group by month and verify all rows are identical
    post_warmup = position.iloc[252:]
    for _month_start, group in post_warmup.groupby(pd.Grouper(freq="MS")):
        if group.empty:
            continue
        first_row = group.iloc[0]
        for _, row in group.iterrows():
            pd.testing.assert_series_equal(
                row,
                first_row,
                check_names=False,
                obj=(
                    "Intra-month position change detected in month "
                    f"starting {_month_start}"
                ),
            )


# ---------------------------------------------------------------------------
# Tests: NaN propagation
# ---------------------------------------------------------------------------


def test_nan_propagated_not_filled() -> None:
    """
    NaN in adapter output must propagate to equity curve without being filled.
    The runner must not call ffill, bfill, fillna, or interpolate.
    """
    n = 500
    closes = np.linspace(100.0, 200.0, n)
    opens = closes + 1.0

    # Inject NaN in open prices at day 300 — affects entry_price at days 298 and 299
    opens[300] = np.nan

    mock_data = _make_mock_ohlcv(["SPY"], closes, opens)

    equity, _ = _run_with_mock(mock_data)

    assert equity.isna().any(), (
        "NaN in adapter output should propagate to equity curve (no filling)"
    )
    # Specifically, days around index 298-299 should have NaN
    # (entry_price[298] = open[299], entry_price[299] = open[300]=NaN)
    assert pd.isna(equity.iloc[298]) or pd.isna(equity.iloc[299]), (
        "NaN at open[300] should cause NaN in returns at surrounding indices"
    )


# ---------------------------------------------------------------------------
# Tests: rebalance count accessibility
# ---------------------------------------------------------------------------


def test_rebalance_count_accessible(caplog: pytest.LogCaptureFixture) -> None:
    """
    The runner must log the IS rebalance count.
    This satisfies PO criterion #5 (rebalance count accessible for reporting).
    """
    with caplog.at_level(logging.INFO, logger="trading.backtest.runner"):
        _run_with_mock(_default_mock())

    log_text = " ".join(r.message for r in caplog.records)
    assert "IS period rebalances" in log_text, (
        "Runner must log 'IS period rebalances: N' for PO traceability"
    )
    assert "WARNING" in log_text, (
        "Runner must warn about low statistical significance in IS period"
    )


# ---------------------------------------------------------------------------
# Tests: metrics delegation
# ---------------------------------------------------------------------------


def test_metrics_delegated_to_equity_metrics() -> None:
    """
    PerformanceReport must be built via compute_performance from equity_metrics,
    not via any custom metric calculation in runner.py.
    """
    sentinel = PerformanceReport(
        sharpe=99.0,
        sortino=88.0,
        calmar=77.0,
        max_drawdown=-0.01,
        max_drawdown_duration=1,
        annualized_return=0.99,
        n_periods=1,
        frequency=Frequency.DAILY,
    )

    with patch(
        "trading.backtest.runner.compute_performance", return_value=sentinel
    ) as mock_cp:
        _, report = _run_with_mock(_default_mock())

    assert mock_cp.called, "compute_performance must be called by run_backtest"
    assert report is sentinel, (
        "run_backtest must return the PerformanceReport from "
        "compute_performance unchanged"
    )
    # Sentinel values can only be present if delegated — runner doesn't recalculate
    assert report.sharpe == 99.0
