from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_uptrend_prices() -> pd.Series[float]:
    """Monthly price series with a clear uptrend (~+1% per month), 36 months."""
    dates = pd.date_range(start="2020-01-01", periods=36, freq="MS")
    prices = [100.0 * (1.01**i) for i in range(36)]
    return pd.Series(prices, index=dates, dtype=float)


@pytest.fixture
def synthetic_downtrend_prices() -> pd.Series[float]:
    """Monthly price series with a clear downtrend (~-1% per month), 36 months."""
    dates = pd.date_range(start="2020-01-01", periods=36, freq="MS")
    prices = [100.0 * (0.99**i) for i in range(36)]
    return pd.Series(prices, index=dates, dtype=float)


@pytest.fixture
def synthetic_universe_prices() -> pd.DataFrame:
    """
    DataFrame with 10 synthetic assets over 500 business days.

    Correlations are controlled: all assets share a common factor,
    making mean correlation moderately positive.
    """
    rng = np.random.default_rng(42)
    n_days = 500
    n_assets = 10
    tickers = [f"ASSET{i:02d}" for i in range(n_assets)]
    dates = pd.date_range(start="2021-01-01", periods=n_days, freq="B")

    # Common market factor plus idiosyncratic noise
    common = rng.normal(0.0, 0.01, n_days)
    idio = rng.normal(0.0, 0.005, (n_days, n_assets))
    daily_returns = common[:, None] + idio
    price_paths = 100.0 * np.exp(np.cumsum(daily_returns, axis=0))

    return pd.DataFrame(price_paths, index=dates, columns=tickers)
