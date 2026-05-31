"""
tests/unit/test_run_is_v4_helpers.py
--------------------------------------
Unit tests for pure helpers in scripts/run_is_v4.py.

All tests are network-free and depend only on the two helper functions:
  - compute_greenspan_concentration
  - evaluate_is_gates
"""
from __future__ import annotations

import math

import pytest
from run_is_v4 import (
    _BIAS_NOTE,
    _SUB_PERIOD_KEYS,
    compute_greenspan_concentration,
    evaluate_is_gates,
)

# ---------------------------------------------------------------------------
# Fixtures: sharpe dicts aligned to _SUB_PERIOD_KEYS
# ---------------------------------------------------------------------------


def _make_sharpes(
    ls_vals: list[float],
    lo_vals: list[float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Build (sharpe_ls, sharpe_lo) dicts from value lists in _SUB_PERIOD_KEYS order."""
    return (
        dict(zip(_SUB_PERIOD_KEYS, ls_vals)),
        dict(zip(_SUB_PERIOD_KEYS, lo_vals)),
    )


# ---------------------------------------------------------------------------
# Tests: compute_greenspan_concentration
# ---------------------------------------------------------------------------


def test_greenspan_concentration_below_threshold() -> None:
    """
    Deltas: 0.2, 0.3, 0.2, 0.2 → sum=0.9, ratio=0.2/0.9 ≈ 0.222 < 0.50 → gate pass.
    """
    sharpe_ls, sharpe_lo = _make_sharpes(
        [1.2, 1.3, 1.2, 1.2],
        [1.0, 1.0, 1.0, 1.0],
    )
    ratio = compute_greenspan_concentration(sharpe_ls, sharpe_lo)
    assert not math.isnan(ratio), "Expected a valid float, got nan"
    assert abs(ratio - 0.2 / 0.9) < 1e-9
    assert ratio < 0.50


def test_greenspan_concentration_above_threshold() -> None:
    """
    Deltas: 0.5, 0.1, 0.1, 0.1 → sum=0.8, ratio=0.5/0.8=0.625 > 0.50 → gate fail.
    """
    sharpe_ls, sharpe_lo = _make_sharpes(
        [1.5, 1.1, 1.1, 1.1],
        [1.0, 1.0, 1.0, 1.0],
    )
    ratio = compute_greenspan_concentration(sharpe_ls, sharpe_lo)
    assert not math.isnan(ratio)
    assert abs(ratio - 0.5 / 0.8) < 1e-9
    assert ratio > 0.50


def test_greenspan_concentration_negative_sum() -> None:
    """
    All deltas negative (sharpe_ls < sharpe_lo) → sum < 0 → returns nan, no exception.
    """
    sharpe_ls, sharpe_lo = _make_sharpes(
        [0.5, 0.5, 0.5, 0.5],
        [1.0, 1.0, 1.0, 1.0],
    )
    result = compute_greenspan_concentration(sharpe_ls, sharpe_lo)
    assert math.isnan(result), f"Expected nan for negative sum of deltas, got {result}"


def test_greenspan_concentration_nan_value() -> None:
    """
    Any sharpe input is NaN → delta is NaN → returns nan, no exception.
    """
    sharpe_ls = {
        _SUB_PERIOD_KEYS[0]: float("nan"),
        **{k: 1.0 for k in _SUB_PERIOD_KEYS[1:]},
    }
    sharpe_lo = {k: 0.8 for k in _SUB_PERIOD_KEYS}
    result = compute_greenspan_concentration(sharpe_ls, sharpe_lo)
    assert math.isnan(result), f"Expected nan when a sharpe input is NaN, got {result}"


# ---------------------------------------------------------------------------
# Tests: evaluate_is_gates
# ---------------------------------------------------------------------------


def _all_pass_inputs() -> tuple[dict[str, float], dict[str, float], float]:
    """Sharpe dicts and net sharpe where all 3 gates pass."""
    sharpe_ls, sharpe_lo = _make_sharpes(
        [1.5, 1.5, 1.5, 1.5],
        [1.2, 1.2, 1.2, 1.2],
    )
    # deltas = [0.3, 0.3, 0.3, 0.3], sum=1.2, ratio=0.25 < 0.50 ✓
    # gate1: 4/4 ≥ 0.15 ✓
    # gate3: net sharpe = 1.5 ≥ 1.4 ✓
    return sharpe_ls, sharpe_lo, 1.5


def test_evaluate_gates_all_pass() -> None:
    """4 deltas ≥ 0.15, concentration < 0.50, net Sharpe 1.5 → all gates pass."""
    sharpe_ls, sharpe_lo, sharpe_net = _all_pass_inputs()
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_net)

    assert result["gate1_subperiod_improvement"] is True
    assert result["gate2_greenspan_ok"] is True
    assert result["gate3_sharpe_net_ok"] is True
    assert result["all_gates_pass"] is True


def test_evaluate_gates_gate1_only_two_pass() -> None:
    """Only 2 of 4 sub-periods have delta ≥ 0.15 → gate1=False, all_gates_pass=False."""
    sharpe_ls, sharpe_lo = _make_sharpes(
        [1.5, 1.5, 1.0, 1.0],
        [1.2, 1.2, 1.0, 1.0],
    )
    # deltas: [0.3, 0.3, 0.0, 0.0] → 2 periods ≥ 0.15
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_ls_net=1.5)

    assert result["gate1_subperiod_improvement"] is False
    assert result["gate1_periods_passing"] == 2
    assert result["all_gates_pass"] is False


def test_evaluate_gates_gate2_fails() -> None:
    """
    Concentration = 0.9/1.5 = 0.60 > 0.50 → gate2=False.
    Gate1 still passes (all 4 deltas ≥ 0.15).
    """
    sharpe_ls, sharpe_lo = _make_sharpes(
        [1.9, 1.2, 1.2, 1.2],
        [1.0, 1.0, 1.0, 1.0],
    )
    # deltas: [0.9, 0.2, 0.2, 0.2], sum=1.5, ratio=0.9/1.5=0.60
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_ls_net=1.5)

    assert result["gate2_greenspan_ok"] is False
    assert abs(result["gate2_concentration_ratio"] - 0.9 / 1.5) < 1e-9
    assert result["all_gates_pass"] is False


def test_evaluate_gates_gate3_fails() -> None:
    """sharpe_ls_net = 1.39 < 1.40 → gate3=False, all_gates_pass=False."""
    sharpe_ls, sharpe_lo, _ = _all_pass_inputs()
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_ls_net=1.39)

    assert result["gate3_sharpe_net_ok"] is False
    assert result["gate3_sharpe_net"] == pytest.approx(1.39)
    assert result["all_gates_pass"] is False


def test_evaluate_gates_bias_note_present() -> None:
    """bias_note must be present and non-empty in every evaluate_is_gates result."""
    sharpe_ls, sharpe_lo, sharpe_net = _all_pass_inputs()
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_net)

    assert "bias_note" in result
    assert isinstance(result["bias_note"], str)
    assert len(result["bias_note"]) > 0
    assert result["bias_note"] == _BIAS_NOTE


def test_evaluate_gates_detail_has_four_keys() -> None:
    """gate1_detail must contain exactly the 4 _SUB_PERIOD_KEYS."""
    sharpe_ls, sharpe_lo, sharpe_net = _all_pass_inputs()
    result = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_net)

    detail = result["gate1_detail"]
    assert len(detail) == 4
    assert set(detail.keys()) == set(_SUB_PERIOD_KEYS)
