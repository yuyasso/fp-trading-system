from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.domain.models import (
    InvalidPriceDataError,
    PositionWeight,
    PriceHistory,
    Signal,
)


def test_signal_invalid_value_raises() -> None:
    with pytest.raises(ValueError):
        Signal(ticker="SPY", date=date(2024, 1, 1), value=1.5)


def test_signal_negative_value_raises() -> None:
    with pytest.raises(ValueError):
        Signal(ticker="SPY", date=date(2024, 1, 1), value=-0.1)


def test_position_weight_invalid_raises() -> None:
    with pytest.raises(ValueError):
        PositionWeight(ticker="SPY", weight=1.5)


def test_price_history_nan_raises() -> None:
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    prices = pd.Series([100.0, np.nan, 102.0, 103.0, 104.0], index=dates)
    with pytest.raises(InvalidPriceDataError):
        PriceHistory(prices=prices)


def test_price_history_valid_passes() -> None:
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=dates)
    ph = PriceHistory(prices=prices)
    assert len(ph.prices) == 5
