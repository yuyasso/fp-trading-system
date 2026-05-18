from __future__ import annotations

import numpy as np
import pandas as pd

from trading.domain.models import InsufficientDataError


def apply_vol_filter(
    weight: float,
    vol_21d: float,
    vol_252d_mean: float,
    threshold: float = 1.5,
) -> float:
    """
    Halves the weight when recent volatility is strictly above the threshold.

    If vol_21d > threshold * vol_252d_mean → weight * 0.5
    Otherwise → weight unchanged.
    """
    if vol_21d > threshold * vol_252d_mean:
        return weight * 0.5
    return weight


def compute_universe_mean_correlation(
    prices: pd.DataFrame,
    window_days: int = 60,
) -> float:
    """
    Computes mean pairwise correlation across the universe.

    Calculates the correlation matrix of log returns over the last
    window_days observations and returns the mean of the upper triangle
    (excluding the diagonal).

    Raises:
        InsufficientDataError: If prices has fewer than 2 columns.
    """
    if prices.shape[1] < 2:
        raise InsufficientDataError(
            "Need at least 2 assets to compute mean correlation"
        )
    arr = np.asarray(prices.to_numpy(), dtype=np.float64)
    log_rets = np.diff(np.log(arr), axis=0)
    recent = log_rets[-window_days:]
    log_ret_df = pd.DataFrame(
        recent, columns=prices.columns
    )
    corr_matrix = log_ret_df.corr()
    corr_values = corr_matrix.to_numpy()
    n = corr_matrix.shape[0]
    total = 0.0
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(corr_values[i, j])
            count += 1
    return total / count


def apply_correlation_breaker(
    weights: dict[str, float],
    mean_corr: float,
    threshold: float = 0.6,
) -> dict[str, float]:
    """
    Halves all weights when mean pairwise correlation exceeds the threshold.

    If mean_corr > threshold → all weights × 0.5
    Otherwise → weights unchanged.

    Note: Both apply_vol_filter and apply_correlation_breaker can act
    simultaneously. The minimum resulting weight for an asset is
    weight_original * 0.25 — this is expected behavior, not a bug.
    """
    if mean_corr > threshold:
        return {ticker: w * 0.5 for ticker, w in weights.items()}
    return dict(weights)
