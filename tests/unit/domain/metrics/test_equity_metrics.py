"""
Tests for domain/metrics/equity_metrics.py — written BEFORE the implementation (TDD).

Conventions:
- All series contain periodic returns (not cumulative equity).
- max_drawdown is negative (e.g. -0.20).
- max_drawdown_duration is measured in series periods.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trading.domain.metrics.equity_metrics import (
    TRADING_DAYS_PER_YEAR,
    Frequency,
    PerformanceReport,
    compute_performance,
    rolling_sharpe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_nan(value: float) -> bool:
    return math.isnan(value)


# ---------------------------------------------------------------------------
# test_empty_series_returns_nan
# ---------------------------------------------------------------------------

def test_empty_series_returns_nan() -> None:
    """Empty series: all ratio fields are NaN, n_periods == 0."""
    report = compute_performance(pd.Series([], dtype=float))

    assert report.n_periods == 0
    assert _is_nan(report.sharpe)
    assert _is_nan(report.sortino)
    assert _is_nan(report.calmar)
    assert _is_nan(report.max_drawdown)
    assert _is_nan(report.annualized_return)


# ---------------------------------------------------------------------------
# test_single_element_series
# ---------------------------------------------------------------------------

def test_single_element_series() -> None:
    """Series with one element: Sharpe is NaN (std undefined with ddof=1), no exception."""
    report = compute_performance(pd.Series([0.05]))

    assert report.n_periods == 1
    assert _is_nan(report.sharpe)


# ---------------------------------------------------------------------------
# test_zero_variance_returns_nan_sharpe
# ---------------------------------------------------------------------------

def test_zero_variance_returns_nan_sharpe() -> None:
    """Constant returns → std == 0 → Sharpe and Sortino are NaN."""
    series = pd.Series([0.05] * 100)
    report = compute_performance(series)

    assert _is_nan(report.sharpe)
    assert _is_nan(report.sortino)


# ---------------------------------------------------------------------------
# test_known_sharpe_value
# ---------------------------------------------------------------------------

def test_known_sharpe_value() -> None:
    """Synthetic series: Sharpe matches analytical value within 1e-6."""
    returns = np.array([0.02, 0.01, -0.01, 0.03, -0.02, 0.00, 0.04, -0.03])
    series = pd.Series(returns)

    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns, ddof=1))
    expected_sharpe = mean_r / std_r * math.sqrt(TRADING_DAYS_PER_YEAR)

    report = compute_performance(series, rf=0.0, freq=Frequency.DAILY)

    assert abs(report.sharpe - expected_sharpe) < 1e-6


# ---------------------------------------------------------------------------
# test_known_max_drawdown
# ---------------------------------------------------------------------------

def test_known_max_drawdown() -> None:
    """
    Hand-calculated drawdown scenario.

    series = [0.10, -0.20, 0.10, 0.20]
    equity  = [1.10,  0.88, 0.968, 1.1616]
    running max = [1.10, 1.10, 1.10, 1.1616]
    drawdowns   = [0,   -0.20, -0.12,  0]

    max_drawdown = -0.20 (at index 1)
    peak at index 0, trough at index 1, recovery at index 3 → duration = 3
    """
    series = pd.Series([0.10, -0.20, 0.10, 0.20])
    report = compute_performance(series)

    assert abs(report.max_drawdown - (-0.20)) < 1e-10
    assert report.max_drawdown_duration == 3


# ---------------------------------------------------------------------------
# test_calmar_zero_drawdown_is_nan
# ---------------------------------------------------------------------------

def test_calmar_zero_drawdown_is_nan() -> None:
    """Monotonically increasing series → max_drawdown == 0 → calmar is NaN."""
    # All positive, different returns so std != 0
    series = pd.Series([0.01, 0.02, 0.015, 0.03, 0.005, 0.025, 0.01, 0.02])
    report = compute_performance(series)

    assert report.max_drawdown == 0.0
    assert _is_nan(report.calmar)
    # Sharpe should be finite (std != 0)
    assert not _is_nan(report.sharpe)


# ---------------------------------------------------------------------------
# test_sortino_uses_target_return
# ---------------------------------------------------------------------------

def test_sortino_uses_target_return() -> None:
    """
    With target_return=0.05, returns in (0, 0.05) count as downside;
    with target_return=0.0 they do not → different Sortino values.
    """
    series = pd.Series([0.02, 0.03, -0.01, 0.04, 0.01])

    report_default = compute_performance(series, target_return=0.0)
    report_high_target = compute_performance(series, target_return=0.05)

    # With a higher target the downside deviation is larger → lower Sortino
    assert not _is_nan(report_default.sortino)
    assert not _is_nan(report_high_target.sortino)
    assert report_high_target.sortino < report_default.sortino


# ---------------------------------------------------------------------------
# test_rolling_sharpe_below_min_periods_is_nan
# ---------------------------------------------------------------------------

def test_rolling_sharpe_below_min_periods_is_nan() -> None:
    """
    window=100, min_periods=60: first 59 positions must be NaN.
    Position 59 (60th element) is the first with enough observations.
    """
    rng = np.random.default_rng(0)
    series = pd.Series(rng.normal(0.001, 0.01, 300))

    result = rolling_sharpe(series, window=100, min_periods=60)

    # Positions 0..58 → NaN (only 1..59 observations, all < 60)
    assert result.iloc[:59].isna().all()
    # Position 59 has exactly 60 observations → not NaN
    assert not math.isnan(result.iloc[59])


# ---------------------------------------------------------------------------
# test_rolling_sharpe_sufficient_data
# ---------------------------------------------------------------------------

def test_rolling_sharpe_sufficient_data() -> None:
    """After min_periods is satisfied, rolling_sharpe returns finite values."""
    rng = np.random.default_rng(1)
    series = pd.Series(rng.normal(0.001, 0.01, 200))

    result = rolling_sharpe(series, window=60, min_periods=60)

    # First 59 are NaN, from 59 onward must have at least some finite values
    assert result.iloc[:59].isna().all()
    assert result.iloc[59:].notna().any()


# ---------------------------------------------------------------------------
# test_frequency_enum_rejected_on_invalid
# ---------------------------------------------------------------------------

def test_frequency_enum_rejected_on_invalid() -> None:
    """Passing an invalid string as freq raises TypeError or ValueError."""
    series = pd.Series([0.01, 0.02, -0.01])

    with pytest.raises((TypeError, ValueError)):
        compute_performance(series, freq="invalid_frequency")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# test_n_periods_matches_series_length
# ---------------------------------------------------------------------------

def test_n_periods_matches_series_length() -> None:
    """n_periods in the report equals len(series)."""
    rng = np.random.default_rng(2)
    for n in [1, 50, 252, 1000]:
        series = pd.Series(rng.normal(0.001, 0.01, n))
        report = compute_performance(series)
        assert report.n_periods == n


# ---------------------------------------------------------------------------
# test_weekly_annualization_factor
# ---------------------------------------------------------------------------

def test_weekly_annualization_factor() -> None:
    """
    Weekly Sharpe uses ann_factor=52; daily uses 252.
    For the same series:
      sharpe_weekly / sharpe_daily == sqrt(52) / sqrt(252)
    """
    rng = np.random.default_rng(3)
    returns = rng.normal(0.001, 0.01, 500)
    series = pd.Series(returns)

    report_daily = compute_performance(series, freq=Frequency.DAILY)
    report_weekly = compute_performance(series, freq=Frequency.WEEKLY)

    expected_ratio = math.sqrt(52) / math.sqrt(TRADING_DAYS_PER_YEAR)
    actual_ratio = report_weekly.sharpe / report_daily.sharpe

    assert abs(actual_ratio - expected_ratio) < 1e-10


# ---------------------------------------------------------------------------
# Additional robustness checks
# ---------------------------------------------------------------------------

def test_performance_report_is_frozen() -> None:
    """PerformanceReport must be immutable (frozen dataclass)."""
    report = compute_performance(pd.Series([0.01, -0.01, 0.02]))

    with pytest.raises((AttributeError, TypeError)):
        report.sharpe = 999.0  # type: ignore[misc]


def test_frequency_is_enum() -> None:
    """Frequency must be an Enum, not a free string."""
    assert isinstance(Frequency.DAILY, Frequency)
    assert isinstance(Frequency.WEEKLY, Frequency)
    assert isinstance(Frequency.MONTHLY, Frequency)
    assert issubclass(Frequency, str)


def test_monthly_annualization_factor() -> None:
    """Monthly Sharpe uses ann_factor=12."""
    rng = np.random.default_rng(4)
    series = pd.Series(rng.normal(0.001, 0.01, 500))

    report_daily = compute_performance(series, freq=Frequency.DAILY)
    report_monthly = compute_performance(series, freq=Frequency.MONTHLY)

    expected_ratio = math.sqrt(12) / math.sqrt(TRADING_DAYS_PER_YEAR)
    actual_ratio = report_monthly.sharpe / report_daily.sharpe

    assert abs(actual_ratio - expected_ratio) < 1e-10


def test_rf_reduces_sharpe() -> None:
    """A positive rf shifts per-period excess return down → lower Sharpe."""
    series = pd.Series([0.001, 0.002, -0.001, 0.003] * 50)

    report_no_rf = compute_performance(series, rf=0.0)
    report_with_rf = compute_performance(series, rf=0.05)

    assert report_with_rf.sharpe < report_no_rf.sharpe


def test_drawdown_duration_no_recovery() -> None:
    """Series that never recovers its peak: duration == len(series) - peak_idx."""
    # Peak at index 0 (equity[0]=1.1), then drawdown, never recovers above 1.1
    series = pd.Series([0.10, -0.50, 0.30, 0.50])
    # equity = [1.10, 0.55, 0.715, 1.0725] — never reaches 1.10 again
    report = compute_performance(series)

    assert report.max_drawdown_duration == 4  # len(series) - 0
