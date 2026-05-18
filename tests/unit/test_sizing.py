from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trading.domain.models import InsufficientDataError, InvalidVolatilityError
from trading.domain.sizing import (
    compute_realized_volatility,
    compute_vol_rolling_mean,
    compute_vol_target_weight,
)


def test_vol_target_weight_normal() -> None:
    # vol 8%, target 10%, signal 1.0 → raw weight 1.25 → clamped to 1.0
    weight = compute_vol_target_weight(signal=1.0, vol_realized=0.08, vol_target=0.10)
    assert weight == pytest.approx(1.0)


def test_vol_target_weight_exact() -> None:
    # vol 20%, target 10%, signal 1.0 → weight = 0.10/0.20 * 1.0 = 0.5
    weight = compute_vol_target_weight(signal=1.0, vol_realized=0.20, vol_target=0.10)
    assert weight == pytest.approx(0.5)


def test_zero_signal_returns_zero_weight() -> None:
    weight = compute_vol_target_weight(signal=0.0, vol_realized=0.15, vol_target=0.10)
    assert weight == pytest.approx(0.0)


def test_vol_zero_raises_invalid_vol() -> None:
    with pytest.raises(InvalidVolatilityError):
        compute_vol_target_weight(signal=1.0, vol_realized=0.0)


def test_realized_vol_annualized_correctly() -> None:
    """
    Constructs prices from known log returns and verifies that the returned
    volatility matches std(log_returns, ddof=1) * sqrt(252).
    """
    rng = np.random.default_rng(99)
    n = 30
    log_rets = rng.normal(0.0, 0.01, n)
    prices = pd.Series(
        100.0 * np.exp(np.concatenate([[0.0], np.cumsum(log_rets)])),
        dtype=float,
    )

    vol = compute_realized_volatility(prices, window_days=21)

    expected = float(np.std(log_rets[-21:], ddof=1) * math.sqrt(252))
    assert abs(vol - expected) < 1e-10


def test_insufficient_window_raises() -> None:
    # Only 5 prices, window_days=21 → need at least 22
    prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], dtype=float)
    with pytest.raises(InsufficientDataError):
        compute_realized_volatility(prices, window_days=21)


def test_vol_rolling_mean_returns_float() -> None:
    rng = np.random.default_rng(7)
    n = 260
    log_rets = rng.normal(0.0, 0.01, n)
    prices = pd.Series(
        100.0 * np.exp(np.concatenate([[0.0], np.cumsum(log_rets)])),
        dtype=float,
    )
    mean_vol = compute_vol_rolling_mean(prices, window_days=21)
    assert isinstance(mean_vol, float)
    assert mean_vol > 0.0


def test_vol_rolling_mean_insufficient_raises() -> None:
    prices = pd.Series([100.0] * 10, dtype=float)
    with pytest.raises(InsufficientDataError):
        compute_vol_rolling_mean(prices, window_days=252)
