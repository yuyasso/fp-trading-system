from __future__ import annotations

from typing import Any

import pandas as pd

from trading.domain.models import InsufficientDataError


def compute_tsmom_signal(
    prices: pd.Series[Any],
    lookback_months: int = 12,
    skip_last_months: int = 1,
) -> float:
    """
    Computes the TSMOM (time-series momentum) signal.

    Returns prices[t - skip_months] / prices[t - lookback_months] - 1.
    If result < 0, returns 0.0 (long-only constraint).

    Args:
        prices: Price series with DatetimeIndex.
        lookback_months: Lookback window in months (denominator offset).
        skip_last_months: Months to skip from end (numerator offset).

    Raises:
        InsufficientDataError: If data does not reach the lookback target date.
    """
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("prices must have a DatetimeIndex")

    last_date: pd.Timestamp = prices.index[-1]
    skip_date = last_date - pd.DateOffset(months=skip_last_months)
    lookback_date = last_date - pd.DateOffset(months=lookback_months)

    skip_slice: pd.Series[Any] = prices[prices.index <= skip_date].dropna()
    lookback_slice: pd.Series[Any] = prices[prices.index <= lookback_date].dropna()

    if skip_slice.empty or lookback_slice.empty:
        raise InsufficientDataError(
            f"Insufficient data: need prices reaching back "
            f"{lookback_months} months from {last_date.date()}"
        )

    price_skip = float(skip_slice.iloc[-1])
    price_lookback = float(lookback_slice.iloc[-1])

    momentum = price_skip / price_lookback - 1.0
    return max(0.0, momentum)


def compute_signals_universe(
    prices: pd.DataFrame,
    lookback_months: int = 12,
    skip_months: int = 1,
) -> dict[str, float]:
    """
    Applies compute_tsmom_signal column by column across the universe.

    Assets with InsufficientDataError are silently skipped; the rest are
    returned. Does NOT abort the full universe on a single asset failure.
    """
    signals: dict[str, float] = {}
    for ticker in prices.columns:
        try:
            signals[str(ticker)] = compute_tsmom_signal(
                prices[ticker],
                lookback_months=lookback_months,
                skip_last_months=skip_months,
            )
        except InsufficientDataError:
            pass
    return signals
