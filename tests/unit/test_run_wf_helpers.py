"""
tests/unit/test_run_wf_helpers.py
----------------------------------
Unit tests for testable helper functions in scripts/run_wf.py.

All tests are pure (no network calls). The __main__ block is NOT tested here.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from run_wf import (
    _FOMC_RATES,
    _IS_START,
    classify_quarter_regime,
    compute_diagnostic_2022q2,
    evaluate_gates,
    generate_wf_windows,
    get_ffr_on_date,
    stationary_block_bootstrap_sharpe_ci,
)

# ---------------------------------------------------------------------------
# get_ffr_on_date
# ---------------------------------------------------------------------------


def test_get_ffr_on_date_known_date() -> None:
    """Date exactly on Dec 15, 2022 decision → upper bound 4.50."""
    result = get_ffr_on_date(date(2022, 12, 20), _FOMC_RATES)
    assert result == 4.50


def test_get_ffr_on_date_between_decisions() -> None:
    """Date between two decisions → forward-fill returns the earlier decision's rate."""
    # Between Jun 16, 2022 (1.75) and Jul 28, 2022 (2.50)
    result = get_ffr_on_date(date(2022, 7, 1), _FOMC_RATES)
    assert result == 1.75


def test_get_ffr_on_date_before_dict() -> None:
    """Date before all entries → returns 0.0."""
    result = get_ffr_on_date(date(2020, 1, 1), _FOMC_RATES)
    assert result == 0.0


# ---------------------------------------------------------------------------
# classify_quarter_regime
# ---------------------------------------------------------------------------


def test_classify_quarter_regime_stress() -> None:
    """Q1 2023 has FFR=4.50% throughout (> 4.0), so 100% stress days → 'stress'."""
    result = classify_quarter_regime(date(2023, 1, 1), date(2023, 3, 31), _FOMC_RATES)
    assert result == "stress"


def test_classify_quarter_regime_normal() -> None:
    """Q1 2021 has no entries in _FOMC_RATES → FFR=0.0% → 'normal'."""
    result = classify_quarter_regime(date(2021, 1, 1), date(2021, 3, 31), _FOMC_RATES)
    assert result == "normal"


def test_classify_quarter_regime_boundary() -> None:
    """
    Construct a window where exactly 40% of business days have FFR > threshold.
    pd.bdate_range('2022-01-03', '2022-01-14') = 10 business days.
    Entry date(2022, 1, 11) sets FFR=5.0 → days 7-10 are stress (4 days = 40%).
    40% >= _STRESS_MIN_FRACTION (0.40) → 'stress'.
    """
    # oos_start=Jan 3, oos_end=Jan 15 → [Jan 3, Jan 14] inclusive = 10 bdays
    fomc_custom = {date(2022, 1, 11): 5.0}
    result = classify_quarter_regime(
        date(2022, 1, 3), date(2022, 1, 15), fomc_custom
    )
    assert result == "stress"


# ---------------------------------------------------------------------------
# generate_wf_windows
# ---------------------------------------------------------------------------


def test_generate_wf_windows_count() -> None:
    """2022-01-01 to 2024-03-31 → exactly 9 windows (2022Q1–Q4, 2023Q1–Q4, 2024Q1)."""
    windows = generate_wf_windows(date(2022, 1, 1), date(2024, 3, 31))
    assert len(windows) == 9


def test_generate_wf_windows_no_overlap() -> None:
    """Consecutive windows must satisfy: window[i+1].oos_start = window[i].oos_end + 1 day."""
    windows = generate_wf_windows(date(2022, 1, 1), date(2024, 3, 31))
    for i in range(len(windows) - 1):
        expected_next_start = windows[i]["oos_end"] + timedelta(days=1)
        assert windows[i + 1]["oos_start"] == expected_next_start, (
            f"Window {i + 1} oos_start {windows[i + 1]['oos_start']} != "
            f"window {i} oos_end + 1d ({expected_next_start})"
        )


def test_generate_wf_windows_is_expanding() -> None:
    """Each successive window must have a later is_end than the previous."""
    windows = generate_wf_windows(date(2022, 1, 1), date(2024, 3, 31))
    for i in range(len(windows) - 1):
        assert windows[i + 1]["is_end"] > windows[i]["is_end"], (
            f"Window {i + 1} is_end {windows[i + 1]['is_end']} not > "
            f"window {i} is_end {windows[i]['is_end']}"
        )


def test_generate_wf_windows_is_start_constant() -> None:
    """Every window must have is_start == _IS_START."""
    windows = generate_wf_windows(date(2022, 1, 1), date(2024, 3, 31))
    for i, win in enumerate(windows):
        assert win["is_start"] == _IS_START, (
            f"Window {i} is_start {win['is_start']} != _IS_START {_IS_START}"
        )


# ---------------------------------------------------------------------------
# stationary_block_bootstrap_sharpe_ci
# ---------------------------------------------------------------------------


def test_block_bootstrap_returns_tuple() -> None:
    """Series of 100 returns → returns (float, float) with lo < hi."""
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.001, 0.015, 100))
    lo, hi = stationary_block_bootstrap_sharpe_ci(returns, n_bootstrap=200, block_size=10)
    assert isinstance(lo, float)
    assert isinstance(hi, float)
    assert not math.isnan(lo)
    assert not math.isnan(hi)
    assert lo < hi


def test_block_bootstrap_mean_ci_contains_true_sharpe() -> None:
    """
    With n=10_000 i.i.d. Normal(mu, sigma) observations, the 95% bootstrap CI
    should contain the true Sharpe = mu/sigma*sqrt(252).
    Fixed seed ensures determinism.
    """
    mu = 0.001
    sigma = 0.02
    true_sharpe = mu / sigma * math.sqrt(252)

    rng = np.random.default_rng(42)
    returns = pd.Series(rng.normal(mu, sigma, 10_000))

    lo, hi = stationary_block_bootstrap_sharpe_ci(returns, n_bootstrap=500, block_size=21)
    assert not math.isnan(lo)
    assert not math.isnan(hi)
    assert lo <= true_sharpe <= hi, (
        f"True Sharpe {true_sharpe:.4f} not in CI [{lo:.4f}, {hi:.4f}]"
    )


def test_block_bootstrap_short_series_returns_nan() -> None:
    """Series with 30 obs (< 2 * block_size=21 → 42) → (nan, nan)."""
    returns = pd.Series(np.random.default_rng(1).normal(0, 0.01, 30))
    lo, hi = stationary_block_bootstrap_sharpe_ci(returns, n_bootstrap=100, block_size=21)
    assert math.isnan(lo)
    assert math.isnan(hi)


# ---------------------------------------------------------------------------
# evaluate_gates
# ---------------------------------------------------------------------------


def test_evaluate_gates_normal_pass() -> None:
    """Normal regime, all criteria met → gate_pass=True, stop_triggered=False."""
    result = evaluate_gates(
        sharpe_oos=1.0, max_dd_oos=0.10, sharpe_is=1.5, regime="normal"
    )
    assert result["gate_pass"] is True
    assert result["stop_triggered"] is False
    assert result["regime"] == "normal"


def test_evaluate_gates_normal_stop() -> None:
    """Normal regime, Sharpe < 0.3 → stop_triggered=True."""
    result = evaluate_gates(
        sharpe_oos=0.2, max_dd_oos=0.05, sharpe_is=1.5, regime="normal"
    )
    assert result["stop_triggered"] is True
    assert result["gate_pass"] is False


def test_evaluate_gates_stress_acceptable() -> None:
    """Stress regime, Sharpe > -0.5 and DD < 0.20 → gate_pass=True, stop=False."""
    result = evaluate_gates(
        sharpe_oos=-0.3, max_dd_oos=0.18, sharpe_is=1.5, regime="stress"
    )
    assert result["gate_pass"] is True
    assert result["stop_triggered"] is False
    assert result["regime"] == "stress"


def test_evaluate_gates_stress_fail_dd() -> None:
    """Stress regime, DD=0.25 > 0.20 → gate_pass=False."""
    result = evaluate_gates(
        sharpe_oos=0.5, max_dd_oos=0.25, sharpe_is=1.5, regime="stress"
    )
    assert result["gate_pass"] is False


def test_evaluate_gates_ratio_computed_correctly() -> None:
    """ratio_oos_is = sharpe_oos / sharpe_is."""
    result = evaluate_gates(
        sharpe_oos=1.0, max_dd_oos=0.10, sharpe_is=2.0, regime="normal"
    )
    assert abs(result["ratio_oos_is"] - 0.5) < 1e-10


def test_evaluate_gates_normal_fail_ratio() -> None:
    """Normal regime with ratio = 0.2 < 0.35 → gate_pass=False."""
    result = evaluate_gates(
        sharpe_oos=1.0, max_dd_oos=0.10, sharpe_is=5.0, regime="normal"
    )
    # ratio = 1.0/5.0 = 0.20 < 0.35 → fail
    assert result["gate_pass"] is False
    assert result["stop_triggered"] is False


# ---------------------------------------------------------------------------
# compute_diagnostic_2022q2
# ---------------------------------------------------------------------------


def _make_close_prices_for_diagnostic() -> pd.DataFrame:
    """
    Synthetic close price DataFrame covering 2021-04 to 2022-05.

    SPY: up  ~10% (positive signal)
    TLT: down ~5% (no signal)
    GLD: up  ~3% (positive signal)
    DBC: up  ~20% (positive signal)
    UUP: up  ~6% (positive signal)
    """
    start = date(2021, 4, 1)
    end = date(2022, 5, 1)
    bdays = [d.date() for d in pd.bdate_range(start, end)]

    n = len(bdays)
    rng = np.random.default_rng(7)
    data = {
        "SPY": 400.0 * np.cumprod(1 + rng.normal(0.0004, 0.005, n)),   # up ~10%
        "TLT": 150.0 * np.cumprod(1 + rng.normal(-0.0002, 0.003, n)),  # down ~5%
        "GLD": 170.0 * np.cumprod(1 + rng.normal(0.0001, 0.004, n)),   # slight up
        "DBC": 20.0 * np.cumprod(1 + rng.normal(0.0008, 0.006, n)),    # up ~20%
        "UUP": 21.0 * np.cumprod(1 + rng.normal(0.0002, 0.002, n)),    # up ~6%
    }
    return pd.DataFrame(data, index=pd.Index(bdays))


def test_diagnostic_2022q2_returns_list() -> None:
    """compute_diagnostic_2022q2 always returns a list (never null)."""
    close = _make_close_prices_for_diagnostic()
    result = compute_diagnostic_2022q2(close)
    assert "tickers_with_positive_signal" in result
    assert isinstance(result["tickers_with_positive_signal"], list)
    assert "note" in result
    assert isinstance(result["note"], str)


def test_diagnostic_2022q2_correct_tickers() -> None:
    """Tickers with price_2022-04-01 > price_2021-04-01 are included."""
    # Build a deterministic DataFrame: SPY and DBC go up, TLT goes down.
    tgt = date(2022, 4, 1)
    lkb = date(2021, 4, 1)
    idx = [lkb, date(2021, 10, 1), tgt, date(2022, 4, 15)]
    df = pd.DataFrame(
        {
            "SPY": [100.0, 105.0, 110.0, 112.0],  # up
            "TLT": [100.0, 98.0, 95.0, 94.0],     # down
            "GLD": [100.0, 102.0, 103.0, 104.0],  # up
            "DBC": [100.0, 110.0, 120.0, 121.0],  # up
            "UUP": [100.0, 103.0, 106.0, 107.0],  # up
        },
        index=pd.Index(idx),
    )
    result = compute_diagnostic_2022q2(df)
    positive = result["tickers_with_positive_signal"]
    assert "SPY" in positive
    assert "GLD" in positive
    assert "DBC" in positive
    assert "UUP" in positive
    assert "TLT" not in positive
