from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading.adapters.yahoo_finance import YahooFinanceProvider
from trading.ports.data_provider import DataProviderError

UNIVERSE_TICKERS = [
    "SPY",  # US equities
    "QQQ",  # US tech
    "EFA",  # International developed
    "EEM",  # Emerging markets
    "TLT",  # Long-term US bonds
    "IEF",  # Intermediate US bonds
    "GLD",  # Gold
    "USO",  # Oil
    "VNQ",  # US real estate
    "DBC",  # Broad commodities
]


@pytest.mark.integration
def test_fetch_spy_returns_non_empty_dataframe() -> None:
    provider = YahooFinanceProvider()
    df = provider.get_adjusted_close(
        ["SPY"],
        start=date(2024, 1, 2),
        end=date(2024, 2, 1),
    )
    assert not df.empty
    assert isinstance(df.index, pd.DatetimeIndex)
    assert pd.api.types.is_float_dtype(df["SPY"])
    assert df["SPY"].notna().any()


@pytest.mark.integration
def test_fetch_full_universe_all_tickers_present() -> None:
    provider = YahooFinanceProvider()
    df = provider.get_adjusted_close(
        UNIVERSE_TICKERS,
        start=date(2024, 1, 2),
        end=date(2024, 2, 1),
    )
    assert not df.empty
    for ticker in UNIVERSE_TICKERS:
        assert ticker in df.columns, f"Missing ticker: {ticker}"


@pytest.mark.integration
def test_unknown_ticker_raises_data_provider_error() -> None:
    provider = YahooFinanceProvider()
    with pytest.raises(DataProviderError):
        provider.get_adjusted_close(
            ["XXXXINVALID"],
            start=date(2024, 1, 2),
            end=date(2024, 2, 1),
        )


@pytest.mark.integration
def test_adjusted_close_used() -> None:
    """
    Verifies that auto_adjust=True is in effect (adjusted close, not raw close).

    Indirect check: AAPL underwent a 4:1 split on 2020-08-31.
    With auto_adjust=True, prices before the split are retroactively divided
    by 4, so the series is continuous near ~$100 rather than jumping to ~$400.
    We download a window spanning that date and confirm the pre-split prices
    are in the adjusted range (< $200), not the raw range (~$400-$500).
    """
    provider = YahooFinanceProvider()
    df = provider.get_adjusted_close(
        ["AAPL"],
        start=date(2020, 8, 24),
        end=date(2020, 9, 4),
    )
    assert not df.empty
    assert pd.api.types.is_float_dtype(df["AAPL"])
    # Adjusted prices around the 2020-08-31 split sit near $120-$130.
    # Raw (unadjusted) pre-split prices were ~$490-$500.
    assert float(df["AAPL"].max()) < 300.0, (
        "Prices look unadjusted — expected adjusted close via auto_adjust=True"
    )
