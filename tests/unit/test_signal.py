from __future__ import annotations

import pandas as pd
import pytest

from trading.domain.models import InsufficientDataError
from trading.domain.signal import compute_signals_universe, compute_tsmom_signal


def test_positive_trend_returns_positive_signal(
    synthetic_uptrend_prices: pd.Series[float],
) -> None:
    signal = compute_tsmom_signal(synthetic_uptrend_prices)
    assert signal > 0.0


def test_negative_trend_returns_zero_long_only(
    synthetic_downtrend_prices: pd.Series[float],
) -> None:
    signal = compute_tsmom_signal(synthetic_downtrend_prices)
    assert signal == 0.0


def test_insufficient_data_raises() -> None:
    # 10 monthly points < 13 required (lookback=12 + skip=1)
    dates = pd.date_range("2020-01-01", periods=10, freq="MS")
    prices = pd.Series([100.0 * (1.01**i) for i in range(10)], index=dates)
    with pytest.raises(InsufficientDataError):
        compute_tsmom_signal(prices, lookback_months=12, skip_last_months=1)


def test_skip_last_month_respected() -> None:
    """
    The signal must use prices[t-1] (skip=1), not prices[t].

    Construct a 14-month series where the last price is inflated to a value
    that would yield a much higher signal if skip were ignored (0 instead of 1).
    Verify that the result uses prices[t-1] / prices[t-12].
    """
    dates = pd.date_range("2020-01-01", periods=14, freq="MS")
    # All prices = 100, except:
    #   dates[1]  → t-12 (denominator)  = 100
    #   dates[12] → t-1  (numerator)    = 110
    #   dates[13] → t    (NOT used)     = 500  (would give signal 4.0 → clamped to 1.0)
    prices = pd.Series([100.0] * 14, index=dates, dtype=float)
    prices.iloc[1] = 100.0
    prices.iloc[12] = 110.0
    prices.iloc[13] = 500.0

    signal = compute_tsmom_signal(prices, lookback_months=12, skip_last_months=1)

    # Expected: 110/100 - 1 = 0.10
    # If skip was ignored: 500/100 - 1 = 4.0 → clamped to 1.0 → would fail assert
    assert abs(signal - 0.10) < 1e-10


def test_exactly_lookback_plus_skip_does_not_raise() -> None:
    # Exactly 13 monthly points: last=dates[12], t-1=dates[11], t-12=dates[0]
    dates = pd.date_range("2020-01-01", periods=13, freq="MS")
    prices = pd.Series(
        [100.0 * (1.01**i) for i in range(13)], index=dates, dtype=float
    )
    result = compute_tsmom_signal(prices, lookback_months=12, skip_last_months=1)
    assert isinstance(result, float)


def test_universe_signals_partial_failure_is_isolated(
    synthetic_uptrend_prices: pd.Series[float],
) -> None:
    """
    An asset with insufficient data must not abort the universe computation.
    The failing asset is absent from the result; the rest are computed normally.

    SHORT starts only 5 months before the shared end date, so its lookback
    target (12 months back) lies before any of its valid prices → InsufficientDataError.
    """
    # GOOD covers 36 months; the shared end date is 2022-12-01
    # SHORT starts 2022-08-01 (only 5 months of history) so lookback=12 is unreachable
    short_dates = pd.date_range("2022-08-01", periods=5, freq="MS")
    short_prices = pd.Series([100.0] * 5, index=short_dates, dtype=float)

    universe = pd.DataFrame(
        {
            "GOOD": synthetic_uptrend_prices,
            "SHORT": short_prices,
        }
    )
    signals = compute_signals_universe(universe)

    assert "GOOD" in signals
    assert "SHORT" not in signals
    assert signals["GOOD"] >= 0.0
