"""
tests/unit/test_run_wf_v3a_helpers.py
---------------------------------------
Unit tests for testable helpers in scripts/run_wf_v3a.py.

All tests are pure (zero network calls). No __main__ block is tested here.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from run_wf_v3a import (
    apply_correlation_overlay,
    compute_correlation_threshold,
    compute_mean_exposure,
    compute_rolling_correlation,
    count_overlay_events,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_random_returns(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, 0.01, n))


def _make_overlay_fixture() -> tuple[pd.DataFrame, pd.Series, float, float]:
    """
    10 business-day DataFrame (2 assets) + correlation series.

    Correlation values: NaN × 2, then 0.3, 0.3, 0.8, 0.9, 0.8, 0.3, 0.2, 0.1.
    Threshold = 0.7, reduction = 0.5.
    Rows 4, 5, 6 are above threshold.
    """
    dates = pd.bdate_range("2022-01-03", periods=10, freq="B")
    vol_weights = pd.DataFrame(
        {"SPY": [1.0] * 10, "TLT": [0.5] * 10},
        index=dates,
    )
    corr = pd.Series(
        [np.nan, np.nan, 0.3, 0.3, 0.8, 0.9, 0.8, 0.3, 0.2, 0.1],
        index=dates,
    )
    return vol_weights, corr, 0.7, 0.5


# ---------------------------------------------------------------------------
# compute_rolling_correlation
# ---------------------------------------------------------------------------


def test_rolling_correlation_same_length() -> None:
    """Output series has the same length as the inputs."""
    returns = _make_random_returns(80)
    window = 15
    result = compute_rolling_correlation(returns, returns, window)
    assert len(result) == len(returns)


def test_rolling_correlation_nan_prefix() -> None:
    """First window-1 positions must be NaN."""
    returns = _make_random_returns(50)
    window = 10
    result = compute_rolling_correlation(returns, returns, window)
    assert result.iloc[: window - 1].isna().all()
    assert not result.iloc[window - 1 :].isna().all()


def test_rolling_correlation_perfect_positive() -> None:
    """Identical return series → correlation = 1.0 at every valid position."""
    returns = _make_random_returns(100)
    window = 10
    result = compute_rolling_correlation(returns, returns, window)
    valid = result.dropna()
    assert len(valid) > 0
    np.testing.assert_allclose(valid.values, 1.0, atol=1e-10)


def test_rolling_correlation_perfect_negative() -> None:
    """Opposite return series → correlation = -1.0 at every valid position."""
    returns = _make_random_returns(100)
    window = 10
    result = compute_rolling_correlation(returns, -returns, window)
    valid = result.dropna()
    assert len(valid) > 0
    np.testing.assert_allclose(valid.values, -1.0, atol=1e-10)


# ---------------------------------------------------------------------------
# compute_correlation_threshold
# ---------------------------------------------------------------------------


def test_threshold_is_scalar_float() -> None:
    """Return value must be a plain Python float, not np.floating."""
    rng = np.random.default_rng(0)
    n = 120
    close_a = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    close_b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    result = compute_correlation_threshold(close_a, close_b, 20, 90.0)
    assert isinstance(result, float)
    assert not isinstance(result, np.floating)


def test_threshold_percentile_value() -> None:
    """compute_correlation_threshold matches np.percentile on the rolling corr."""
    rng = np.random.default_rng(7)
    n = 200
    close_a = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    close_b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    window = 20
    result = compute_correlation_threshold(close_a, close_b, window, 90.0)
    # Compute expected independently
    corr = close_a.pct_change().rolling(window).corr(close_b.pct_change())
    expected = float(np.percentile(corr.dropna().values, 90.0))
    assert abs(result - expected) < 1e-10


# ---------------------------------------------------------------------------
# apply_correlation_overlay
# ---------------------------------------------------------------------------


def test_overlay_reduces_when_above() -> None:
    """Days with correlation > threshold have weights multiplied by reduction_factor."""
    vol_weights, corr, threshold, reduction = _make_overlay_fixture()
    result = apply_correlation_overlay(vol_weights, corr, threshold, reduction)
    # Rows 4, 5, 6 have corr (0.8, 0.9, 0.8) > 0.7 → SPY: 1.0 × 0.5 = 0.5
    assert result.iloc[4]["SPY"] == pytest.approx(0.5)
    assert result.iloc[5]["SPY"] == pytest.approx(0.5)
    assert result.iloc[6]["SPY"] == pytest.approx(0.5)
    assert result.iloc[4]["TLT"] == pytest.approx(0.25)


def test_overlay_no_change_when_below() -> None:
    """Days with correlation ≤ threshold are not modified."""
    vol_weights, corr, threshold, reduction = _make_overlay_fixture()
    result = apply_correlation_overlay(vol_weights, corr, threshold, reduction)
    # Rows 2, 3, 7, 8, 9 have corr (0.3, 0.3, 0.3, 0.2, 0.1) ≤ 0.7
    for row in (2, 3, 7, 8, 9):
        assert result.iloc[row]["SPY"] == pytest.approx(1.0), f"Row {row} was changed"
        assert result.iloc[row]["TLT"] == pytest.approx(0.5), f"Row {row} TLT changed"


def test_overlay_nan_corr_leaves_weights_unchanged() -> None:
    """Rows with NaN correlation are treated as not active → weights unchanged."""
    vol_weights, corr, threshold, reduction = _make_overlay_fixture()
    result = apply_correlation_overlay(vol_weights, corr, threshold, reduction)
    # Rows 0, 1 have NaN correlation
    assert result.iloc[0]["SPY"] == pytest.approx(1.0)
    assert result.iloc[1]["SPY"] == pytest.approx(1.0)


def test_overlay_preserves_shape() -> None:
    """Output DataFrame has the same shape, columns, and index as input."""
    vol_weights, corr, threshold, reduction = _make_overlay_fixture()
    result = apply_correlation_overlay(vol_weights, corr, threshold, reduction)
    assert result.shape == vol_weights.shape
    assert list(result.columns) == list(vol_weights.columns)
    assert list(result.index) == list(vol_weights.index)


# ---------------------------------------------------------------------------
# count_overlay_events
# ---------------------------------------------------------------------------


def test_count_events_single_zone() -> None:
    """A single contiguous active zone → exactly 1 event."""
    dates = pd.bdate_range("2022-01-03", periods=10, freq="B")
    corr = pd.Series(
        [0.9, 0.9, 0.9, 0.9, 0.9, 0.3, 0.3, 0.3, 0.3, 0.3],
        index=dates,
    )
    events = count_overlay_events(corr, threshold=0.7)
    assert len(events) == 1
    assert isinstance(events[0][0], date)
    assert isinstance(events[0][1], date)


def test_count_events_gap_separates() -> None:
    """Two zones separated by > 20 calendar days → 2 distinct events."""
    base = date(2022, 1, 3)
    # Zone 1: days 0-4 (Jan 3-7)
    zone1 = {base + timedelta(days=i) for i in range(5)}
    # Zone 2: starts Jan 3 + 30 days = Feb 2 (gap of 26 days from Jan 7)
    zone2_start = base + timedelta(days=30)
    zone2 = {zone2_start + timedelta(days=i) for i in range(5)}
    # Inactive gap days
    gap = {base + timedelta(days=i) for i in range(5, 30)}
    all_dates = sorted(zone1 | gap | zone2)
    values = [0.9 if d in (zone1 | zone2) else 0.3 for d in all_dates]
    corr = pd.Series(values, index=pd.Index(all_dates))
    events = count_overlay_events(corr, threshold=0.7, min_gap_days=20)
    assert len(events) == 2


def test_count_events_gap_merges() -> None:
    """Two zones separated by < 20 calendar days → merged into 1 event."""
    base = date(2022, 1, 3)
    # Zone 1: days 0-4 (Jan 3-7)
    zone1 = {base + timedelta(days=i) for i in range(5)}
    # Zone 2: starts Jan 3 + 15 days = Jan 18 (gap of 11 days from Jan 7 < 20)
    zone2_start = base + timedelta(days=15)
    zone2 = {zone2_start + timedelta(days=i) for i in range(5)}
    gap = {base + timedelta(days=i) for i in range(5, 15)}
    all_dates = sorted(zone1 | gap | zone2)
    values = [0.9 if d in (zone1 | zone2) else 0.3 for d in all_dates]
    corr = pd.Series(values, index=pd.Index(all_dates))
    events = count_overlay_events(corr, threshold=0.7, min_gap_days=20)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# compute_mean_exposure
# ---------------------------------------------------------------------------


def test_exposure_fully_invested() -> None:
    """All days have positive weight → exposure = 1.0."""
    dates = pd.bdate_range("2022-01-03", periods=10, freq="B")
    weights = pd.DataFrame({"SPY": [0.5] * 10, "TLT": [0.3] * 10}, index=dates)
    start = dates[0].date()
    end = dates[-1].date()
    result = compute_mean_exposure(weights, start, end)
    assert result == pytest.approx(1.0)


def test_exposure_half_invested() -> None:
    """First half of days have positive weight, second half zero → exposure = 0.5."""
    dates = pd.bdate_range("2022-01-03", periods=10, freq="B")
    weights = pd.DataFrame(
        {"SPY": [0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]},
        index=dates,
    )
    start = dates[0].date()
    end = dates[-1].date()
    result = compute_mean_exposure(weights, start, end)
    assert result == pytest.approx(0.5)


def test_exposure_empty_range() -> None:
    """Range entirely outside available data → float (nan), no exception raised."""
    dates = pd.bdate_range("2022-01-03", periods=10, freq="B")
    weights = pd.DataFrame({"SPY": [0.5] * 10}, index=dates)
    result = compute_mean_exposure(weights, date(2025, 1, 1), date(2025, 3, 31))
    assert isinstance(result, float)
    # Must not raise; result is nan (empty slice) or 0.0
    assert math.isnan(result) or result == 0.0
