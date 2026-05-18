from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading.domain.filters import (
    apply_correlation_breaker,
    apply_vol_filter,
    compute_universe_mean_correlation,
)
from trading.domain.models import InsufficientDataError

# ---------------------------------------------------------------------------
# Vol filter
# ---------------------------------------------------------------------------


def test_vol_filter_triggers_at_strictly_greater() -> None:
    # vol_21d/vol_252d_mean = 0.201/0.100 = 2.01 > threshold 1.5 → triggers
    weight = apply_vol_filter(
        weight=1.0, vol_21d=0.201, vol_252d_mean=0.100, threshold=1.5
    )
    assert weight == pytest.approx(0.5)


def test_vol_filter_no_trigger_at_boundary() -> None:
    # vol_21d = 1.5 * vol_252d_mean exactly → NOT strictly greater → no trigger
    weight = apply_vol_filter(
        weight=1.0, vol_21d=0.150, vol_252d_mean=0.100, threshold=1.5
    )
    assert weight == pytest.approx(1.0)


def test_vol_filter_no_trigger_below() -> None:
    weight = apply_vol_filter(
        weight=0.8, vol_21d=0.12, vol_252d_mean=0.10, threshold=1.5
    )
    assert weight == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Correlation breaker
# ---------------------------------------------------------------------------


def test_corr_breaker_triggers_at_strictly_greater() -> None:
    weights = {"SPY": 0.6, "TLT": 0.4}
    result = apply_correlation_breaker(weights, mean_corr=0.61, threshold=0.6)
    assert result["SPY"] == pytest.approx(0.3)
    assert result["TLT"] == pytest.approx(0.2)


def test_corr_breaker_no_trigger_at_boundary() -> None:
    weights = {"SPY": 0.6, "TLT": 0.4}
    result = apply_correlation_breaker(weights, mean_corr=0.60, threshold=0.6)
    assert result["SPY"] == pytest.approx(0.6)
    assert result["TLT"] == pytest.approx(0.4)


def test_both_filters_combined_quarter_weight() -> None:
    """
    Both filters firing on a weight of 1.0 yield 0.25 (1.0 * 0.5 * 0.5).
    This is expected behavior, not a bug.
    """
    weight = 1.0

    # Vol filter fires
    after_vol = apply_vol_filter(
        weight=weight, vol_21d=0.30, vol_252d_mean=0.10, threshold=1.5
    )
    assert after_vol == pytest.approx(0.5)

    # Correlation breaker fires on the already-reduced weight
    after_corr = apply_correlation_breaker(
        {"ASSET": after_vol}, mean_corr=0.61, threshold=0.6
    )
    assert after_corr["ASSET"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Mean correlation
# ---------------------------------------------------------------------------


def test_mean_correlation_known_matrix() -> None:
    """
    Three assets with analytically known correlations:
      - A and B: perfectly correlated  (corr = +1.0)
      - A and C: perfectly anti-correlated (corr = -1.0)
      - B and C: perfectly anti-correlated (corr = -1.0)

    Mean of upper triangle = (1 + (-1) + (-1)) / 3 = -1/3
    """
    n = 100
    dates = pd.date_range("2020-01-01", periods=n + 1, freq="B")
    rng = np.random.default_rng(0)
    base = rng.normal(0.0, 0.01, n)

    prices_a = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(base)]))
    prices_b = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(base)]))  # == A
    prices_c = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(-base)]))  # anti-A

    df = pd.DataFrame(
        {"A": prices_a, "B": prices_b, "C": prices_c}, index=dates
    )

    mean_corr = compute_universe_mean_correlation(df, window_days=n)
    assert abs(mean_corr - (-1.0 / 3.0)) < 1e-10


def test_mean_correlation_insufficient_assets() -> None:
    dates = pd.date_range("2020-01-01", periods=30, freq="B")
    df = pd.DataFrame({"ONLY": 100.0 * np.ones(30)}, index=dates)
    with pytest.raises(InsufficientDataError):
        compute_universe_mean_correlation(df)
