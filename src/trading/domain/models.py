from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


class TradingDomainError(Exception):
    pass


class InsufficientDataError(TradingDomainError):
    pass


class InvalidVolatilityError(TradingDomainError):
    pass


class InvalidPriceDataError(TradingDomainError):
    pass


@dataclass(frozen=True)
class Asset:
    ticker: str
    asset_class: str


@dataclass
class PriceHistory:
    prices: pd.Series[Any]

    def __post_init__(self) -> None:
        if bool(self.prices.isna().any()):
            raise InvalidPriceDataError(
                "Price series contains undocumented NaN values"
            )


@dataclass(frozen=True)
class Signal:
    ticker: str
    date: date
    value: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(
                f"Signal value must be in [0.0, 1.0], got {self.value}"
            )


@dataclass(frozen=True)
class PositionWeight:
    ticker: str
    weight: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError(
                f"Weight must be in [0.0, 1.0], got {self.weight}"
            )
