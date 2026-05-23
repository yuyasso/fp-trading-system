"""
domain/ports/data_source.py
---------------------------
Port defining the OHLCV data loading contract.

Pure domain: no infrastructure imports (no yfinance, requests, httpx, etc.).
Implementations live in adapters/ and are injected at the composition root.
"""
from __future__ import annotations

import datetime
from typing import Protocol

import pandas as pd


class DataSourcePort(Protocol):  # pragma: no cover
    def load_ohlcv_daily(
        self,
        tickers: list[str],
        start: datetime.date,
        end: datetime.date,
    ) -> pd.DataFrame:
        """
        Retorna DataFrame con MultiIndex (date: datetime.date, ticker: str) ordenado
        por date ASC. Columnas exactas: [open, high, low, close, volume], todas float64.
        NaN propagados sin modificar. close es precio ajustado por splits y dividendos.
        """
        ...
