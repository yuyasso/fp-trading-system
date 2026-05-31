"""
tests/unit/test_run_is_v5_helpers.py
--------------------------------------
Unit tests for pure helpers in scripts/run_is_v5.py.

All tests are network-free. YFinanceAdapter is mocked where needed.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from run_is_v5 import (
    _SUB_PERIOD_KEYS,
    compute_fx_concentration,
    compute_greenspan_concentration,
    compute_ls_asset_attribution,
    evaluate_is_gates_v5,
)

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _make_deltas(vals: list[float]) -> dict[str, float]:
    """Build a subperiod_deltas dict from 4 values in _SUB_PERIOD_KEYS order."""
    return dict(zip(_SUB_PERIOD_KEYS, vals))


def _make_sharpes(
    ls_vals: list[float],
    lo_vals: list[float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Build (sharpe_ls, sharpe_lo) dicts from value lists in _SUB_PERIOD_KEYS order."""
    return (
        dict(zip(_SUB_PERIOD_KEYS, ls_vals)),
        dict(zip(_SUB_PERIOD_KEYS, lo_vals)),
    )


def _make_mock_ohlcv(
    tickers: list[str],
    closes: np.ndarray,
    opens: np.ndarray,
) -> pd.DataFrame:
    """Build a (date, ticker) MultiIndex DataFrame mimicking YFinanceAdapter output."""
    n = len(closes)
    dates = [d.date() for d in pd.date_range("2015-01-02", periods=n, freq="B")]

    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        highs = np.maximum(opens, closes) * 1.002
        lows = np.minimum(opens, closes) * 0.998
        volumes = np.ones(n) * 1_000_000.0
        midx = pd.MultiIndex.from_arrays(
            [dates, [ticker] * n],
            names=["date", "ticker"],
        )
        frames.append(
            pd.DataFrame(
                {
                    "open": opens.astype(float),
                    "high": highs,
                    "low": lows,
                    "close": closes.astype(float),
                    "volume": volumes,
                },
                index=midx,
            )
        )
    return pd.concat(frames).sort_index()


def _default_mock(n: int = 500, tickers: list[str] | None = None) -> pd.DataFrame:
    """Standard uptrending mock: closes 100→200 linearly, opens = closes + 0.5."""
    if tickers is None:
        tickers = ["SPY", "TLT"]
    closes = np.linspace(100.0, 200.0, n)
    opens = closes + 0.5
    return _make_mock_ohlcv(tickers, closes, opens)


# ---------------------------------------------------------------------------
# Tests: compute_greenspan_concentration
# ---------------------------------------------------------------------------


def test_greenspan_concentration_happy_path() -> None:
    """
    Deltas: 0.2, 0.4, 0.2, 0.2 → sum=1.0, ratio=0.2/1.0=0.20.
    Verifies correct value to 1e-6 precision.
    """
    deltas = _make_deltas([0.2, 0.4, 0.2, 0.2])
    result = compute_greenspan_concentration(deltas)
    assert result is not None
    assert abs(result - 0.2 / 1.0) < 1e-6
    assert result < 0.50


def test_greenspan_concentration_nonpositive_sum() -> None:
    """Sum of all deltas ≤ 0 → returns None."""
    deltas = _make_deltas([-0.1, -0.2, 0.1, -0.1])  # sum = -0.3
    result = compute_greenspan_concentration(deltas)
    assert result is None


def test_greenspan_concentration_zero_sum() -> None:
    """Exactly-zero sum → returns None."""
    deltas = _make_deltas([0.5, -0.5, 0.0, 0.0])
    result = compute_greenspan_concentration(deltas)
    assert result is None


def test_greenspan_concentration_nan_input() -> None:
    """Any NaN value in deltas → returns None."""
    deltas = _make_deltas([float("nan"), 0.3, 0.2, 0.2])
    result = compute_greenspan_concentration(deltas)
    assert result is None


# ---------------------------------------------------------------------------
# Tests: evaluate_is_gates_v5
# ---------------------------------------------------------------------------


def test_evaluate_is_gates_v5_all_pass() -> None:
    """4 deltas ≥ 0.15, concentration < 0.5, sharpe_total 1.5 → all_gates_pass=True."""
    ls_sharpes, lo_sharpes = _make_sharpes(
        [1.5, 1.5, 1.5, 1.5],
        [1.2, 1.2, 1.2, 1.2],
    )
    # deltas = [0.3, 0.3, 0.3, 0.3], sum=1.2, ratio=0.25 < 0.5 ✓
    result = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, ls_sharpe_total=1.5)

    assert result["gate1_pass"] is True
    assert result["gate1_count"] == 4
    assert result["gate2_pass"] is True
    assert result["gate3_pass"] is True
    assert result["all_gates_pass"] is True


def test_evaluate_is_gates_v5_gate1_fails() -> None:
    """Only 2 sub-periods with delta ≥ 0.15 → gate1_pass=False, all_gates_pass=False."""
    ls_sharpes, lo_sharpes = _make_sharpes(
        [1.5, 1.5, 1.0, 1.0],
        [1.2, 1.2, 1.0, 1.0],
    )
    # deltas: [0.3, 0.3, 0.0, 0.0] → only 2 pass threshold
    result = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, ls_sharpe_total=1.5)

    assert result["gate1_pass"] is False
    assert result["gate1_count"] == 2
    assert result["all_gates_pass"] is False


def test_evaluate_is_gates_v5_gate3_fails() -> None:
    """All deltas ≥ 0.15, Gate 2 passes, sharpe_total=1.2 < 1.4 → gate3_pass=False."""
    ls_sharpes, lo_sharpes = _make_sharpes(
        [1.5, 1.5, 1.5, 1.5],
        [1.2, 1.2, 1.2, 1.2],
    )
    result = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, ls_sharpe_total=1.2)

    assert result["gate3_pass"] is False
    assert result["gate3_sharpe"] == pytest.approx(1.2)
    assert result["all_gates_pass"] is False


def test_evaluate_is_gates_v5_gate2_none() -> None:
    """Sum of gate1 deltas ≤ 0 → gate2_concentration=None, gate2_pass=False."""
    ls_sharpes, lo_sharpes = _make_sharpes(
        [0.5, 0.5, 0.5, 0.5],
        [1.0, 1.0, 1.0, 1.0],
    )
    # all deltas = -0.5 → sum = -2.0 ≤ 0
    result = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, ls_sharpe_total=1.5)

    assert result["gate2_concentration"] is None
    assert result["gate2_pass"] is False
    assert result["all_gates_pass"] is False


def test_gate1_deltas_dict_keys() -> None:
    """gate1_deltas must contain exactly the 4 _SUB_PERIOD_KEYS."""
    ls_sharpes, lo_sharpes = _make_sharpes(
        [1.5, 1.5, 1.5, 1.5],
        [1.2, 1.2, 1.2, 1.2],
    )
    result = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, ls_sharpe_total=1.5)

    assert set(result["gate1_deltas"].keys()) == set(_SUB_PERIOD_KEYS)
    assert len(result["gate1_deltas"]) == 4


# ---------------------------------------------------------------------------
# Tests: compute_fx_concentration
# ---------------------------------------------------------------------------


def _make_attribution(
    tickers: list[str],
    date_ranges: list[tuple[str, str]],
    daily_values: list[float],
) -> pd.DataFrame:
    """
    Build a synthetic attribution DataFrame.

    For each date-range bucket, fill all tickers with the corresponding scalar value.
    """
    frames = []
    for (start_iso, end_iso), val in zip(date_ranges, daily_values):
        dates = [
            d.date()
            for d in pd.date_range(start_iso, end_iso, freq="B")
        ]
        df = pd.DataFrame(
            {t: val for t in tickers},
            index=dates,
        )
        frames.append(df)
    return pd.concat(frames)


def test_fx_concentration_not_concentrated() -> None:
    """
    Equal FX contribution across 4 sub-periods → each ~25% share → concentrated=False.

    All sub-periods: FXE=FXY=0.001 in LS, 0.0 in LO.
    Sub-period lengths differ (4y, 5y, 5y, 3y) but the uniform daily rate makes
    each contribution proportional to length; max share < 50%.
    """
    sub_periods = [
        ("2005-01-03", "2008-12-31"),
        ("2009-01-02", "2013-12-31"),
        ("2014-01-02", "2018-12-31"),
        ("2019-01-02", "2021-12-31"),
    ]
    # uniform LS FX return; no LO FX contribution
    attribution_ls = _make_attribution(
        ["FXE", "FXY", "SPY"],
        sub_periods,
        [0.001, 0.001, 0.001, 0.001],
    )
    attribution_lo = _make_attribution(
        ["SPY"],
        sub_periods,
        [0.0, 0.0, 0.0, 0.0],
    )

    result = compute_fx_concentration(
        {},
        attribution_ls,
        attribution_lo,
        sub_periods,
    )

    assert result["concentrated"] is False
    assert result["subperiod_max_fx_share"] < 0.50
    assert result["subperiod_max_fx_label"] != ""


def test_fx_concentration_concentrated() -> None:
    """
    Sub-period 1 has 10× larger FX daily return → its share > 50% → concentrated=True.
    """
    sub_periods = [
        ("2019-01-02", "2019-03-29"),
        ("2019-04-01", "2019-06-28"),
        ("2019-07-01", "2019-09-30"),
        ("2019-10-01", "2019-12-31"),
    ]
    # Period 1: FX = 0.10, others: FX = 0.001 → period 1 dominates
    attribution_ls = _make_attribution(
        ["FXE", "FXY"],
        sub_periods,
        [0.10, 0.001, 0.001, 0.001],
    )
    attribution_lo = _make_attribution(
        ["SPY"],  # no FX in LO → lo_fx_mean = 0
        sub_periods,
        [0.0, 0.0, 0.0, 0.0],
    )

    result = compute_fx_concentration(
        {},
        attribution_ls,
        attribution_lo,
        sub_periods,
    )

    assert result["concentrated"] is True
    assert result["subperiod_max_fx_share"] > 0.50
    assert result["subperiod_max_fx_label"] == "2019-19"


# ---------------------------------------------------------------------------
# Tests: compute_ls_asset_attribution (with mocked adapter)
# ---------------------------------------------------------------------------


def test_compute_ls_asset_attribution_single_ticker() -> None:
    """Single ticker → result is a DataFrame with exactly one column."""
    tickers = ["SPY"]
    mock_data = _default_mock(n=400, tickers=tickers)

    with patch(
        "run_is_v5.YFinanceAdapter",
        return_value=MagicMock(load_ohlcv_daily=MagicMock(return_value=mock_data)),
    ):
        result = compute_ls_asset_attribution(
            tickers=tickers,
            start=date(2015, 1, 1),
            end=date(2016, 12, 31),
            borrow_costs={"SPY": 0.001},
            lookback_months=12,
            target_vol=0.10,
        )

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["SPY"]


def test_compute_ls_asset_attribution_nan_propagates() -> None:
    """
    NaN in open price at row i propagates to attribution row i or i+1
    (entry_price = open.shift(-1), return = entry.shift(-1)/entry - 1).
    """
    tickers = ["SPY", "TLT"]
    mock_data = _default_mock(n=400, tickers=tickers)

    # Inject NaN into open at row 300 for all tickers
    nan_row_date = mock_data.index.get_level_values("date").unique()[300]
    mock_data.loc[(nan_row_date, slice(None)), "open"] = float("nan")

    with patch(
        "run_is_v5.YFinanceAdapter",
        return_value=MagicMock(load_ohlcv_daily=MagicMock(return_value=mock_data)),
    ):
        result = compute_ls_asset_attribution(
            tickers=tickers,
            start=date(2015, 1, 1),
            end=date(2016, 12, 31),
            borrow_costs={},
            lookback_months=12,
            target_vol=0.10,
        )

    # NaN should propagate into the attribution around the injected row
    all_values = result.values.flatten()
    assert any(np.isnan(v) for v in all_values), (
        "Expected NaN to propagate from open price into attribution"
    )
