from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trading.domain.models import InsufficientDataError, InvalidVolatilityError


def compute_realized_volatility(
    prices: pd.Series[Any], window_days: int = 21
) -> float:
    """
    Returns annualized realized volatility: std(log_returns[-window_days:]) * sqrt(252).

    Raises:
        InsufficientDataError: If len(prices) < window_days + 1.
    """
    if len(prices) < window_days + 1:
        raise InsufficientDataError(
            f"Need at least {window_days + 1} prices to compute "
            f"{window_days}-day vol, got {len(prices)}"
        )
    arr = np.asarray(prices.to_numpy(), dtype=np.float64)
    log_rets = np.diff(np.log(arr))
    recent = log_rets[-window_days:]
    return float(np.std(recent, ddof=1) * math.sqrt(252))


def compute_vol_rolling_mean(
    prices: pd.Series[Any], window_days: int = 252
) -> float:
    """
    Returns mean of daily rolling volatilities over the long window.

    Each rolling vol is std(log_returns[t-window:t]) * sqrt(252).

    Raises:
        InsufficientDataError: If len(prices) < window_days + 1.
    """
    if len(prices) < window_days + 1:
        raise InsufficientDataError(
            f"Need at least {window_days + 1} prices for rolling vol mean, "
            f"got {len(prices)}"
        )
    arr = np.asarray(prices.to_numpy(), dtype=np.float64)
    log_rets = np.diff(np.log(arr))
    log_ret_series: pd.Series[Any] = pd.Series(log_rets)
    rolling_vol: pd.Series[Any] = (
        log_ret_series.rolling(window=window_days).std() * math.sqrt(252)
    )
    return float(rolling_vol.dropna().mean())


def compute_vol_target_weight(
    signal: float,
    vol_realized: float,
    vol_target: float = 0.10,
) -> float:
    """
    Computes vol-targeted position weight.

    weight = (vol_target / vol_realized) * signal, clamped to [0.0, 1.0].

    Raises:
        InvalidVolatilityError: If vol_realized <= 0.
    """
    if vol_realized <= 0:
        raise InvalidVolatilityError(
            f"vol_realized must be > 0, got {vol_realized}"
        )
    weight = (vol_target / vol_realized) * signal
    return max(0.0, min(1.0, weight))
