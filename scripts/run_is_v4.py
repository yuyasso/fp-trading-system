"""
scripts/run_is_v4.py
--------------------
IS runner: TSMOM long/short v4 on SPY+TLT+GLD with explicit borrow costs.

Evaluates 3 pre-registered IS gates and compares L/S vs long-only by sub-period.
The if __name__ == "__main__" block requires network access; helpers are pure/mockable.

ADR: L/S designed knowing 2022 failed long-only. Justification: Moskowitz et al.
2012 economic rationale (momentum symmetric). Sub-period gate mitigates selection bias.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from run_is import compute_subperiod_sharpes

from trading.backtest.runner import run_backtest_ls, run_backtest_range
from trading.domain.metrics.equity_metrics import compute_performance

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_TICKERS = ["SPY", "TLT", "GLD"]
_LOOKBACK_MONTHS = 12
_TARGET_VOL = 0.10
_IS_START = date(2005, 1, 1)
_IS_END = date(2021, 12, 31)
_BORROW_COSTS: dict[str, float] = {"SPY": 0.000, "TLT": 0.001, "GLD": 0.004}

_BIAS_NOTE = (
    "ADR: L/S designed knowing 2022 failed long-only. "
    "Justification: Moskowitz et al. 2012 economic rationale (momentum symmetric). "
    "Sub-period gate mitigates selection bias."
)

_SUB_PERIODS = [
    ("2005-01-01", "2008-12-31"),
    ("2009-01-01", "2013-12-31"),
    ("2014-01-01", "2018-12-31"),
    ("2019-01-01", "2021-12-31"),
]

_SUB_PERIOD_KEYS = ["2005-08", "2009-13", "2014-18", "2019-21"]


# ---------------------------------------------------------------------------
# Helper 1: Greenspan concentration ratio
# ---------------------------------------------------------------------------


def compute_greenspan_concentration(
    sharpe_ls: dict[str, float],
    sharpe_lo: dict[str, float],
) -> float:
    """
    Compute the Greenspan concentration ratio for the 2005-08 sub-period.

    Concentration = delta["2005-08"] / sum(all 4 deltas),
    where delta[k] = sharpe_ls[k] - sharpe_lo[k].

    Returns float("nan") if:
    - Any delta is NaN (i.e. any input sharpe is NaN)
    - Sum of all 4 deltas ≤ 0 (including exactly 0)

    Parameters
    ----------
    sharpe_ls : dict[str, float]
        Sub-period Sharpe ratios for the L/S strategy, keyed by _SUB_PERIOD_KEYS.
    sharpe_lo : dict[str, float]
        Sub-period Sharpe ratios for the long-only strategy, keyed by _SUB_PERIOD_KEYS.

    Returns
    -------
    float
        Concentration ratio ∈ (0, 1], or float("nan") on invalid input.
    """
    deltas: list[float] = []
    for k in _SUB_PERIOD_KEYS:
        ls_val = sharpe_ls.get(k, float("nan"))
        lo_val = sharpe_lo.get(k, float("nan"))
        if math.isnan(ls_val) or math.isnan(lo_val):
            return float("nan")
        deltas.append(ls_val - lo_val)

    total = sum(deltas)
    if total <= 0:
        return float("nan")

    return deltas[0] / total  # deltas[0] = delta["2005-08"]


# ---------------------------------------------------------------------------
# Helper 2: IS gate evaluation
# ---------------------------------------------------------------------------


def evaluate_is_gates(
    sharpe_ls: dict[str, float],
    sharpe_lo: dict[str, float],
    sharpe_ls_net: float,
) -> dict:
    """
    Evaluate the 3 pre-registered IS gates for the L/S strategy.

    Gate 1: ≥ 3 of 4 sub-periods with delta(sharpe_ls - sharpe_lo) ≥ 0.15
    Gate 2: Greenspan concentration ratio < 0.50
    Gate 3: Net IS Sharpe ≥ 1.40

    Parameters
    ----------
    sharpe_ls : dict[str, float]
        Sub-period Sharpe ratios for L/S, keyed by _SUB_PERIOD_KEYS.
    sharpe_lo : dict[str, float]
        Sub-period Sharpe ratios for long-only, keyed by _SUB_PERIOD_KEYS.
    sharpe_ls_net : float
        Full-period net IS Sharpe ratio for the L/S strategy.

    Returns
    -------
    dict with keys:
        gate1_subperiod_improvement, gate1_periods_passing, gate1_detail,
        gate2_greenspan_ok, gate2_concentration_ratio,
        gate3_sharpe_net_ok, gate3_sharpe_net,
        all_gates_pass, bias_note.
    """
    # Gate 1: sub-period improvement
    gate1_detail = {
        k: float(sharpe_ls.get(k, float("nan"))) - float(sharpe_lo.get(k, float("nan")))
        for k in _SUB_PERIOD_KEYS
    }
    gate1_periods_passing = sum(
        1 for d in gate1_detail.values() if not math.isnan(d) and d >= 0.15
    )
    gate1 = bool(gate1_periods_passing >= 3)

    # Gate 2: Greenspan concentration
    concentration = compute_greenspan_concentration(sharpe_ls, sharpe_lo)
    gate2 = bool(not math.isnan(concentration) and concentration < 0.50)

    # Gate 3: net IS Sharpe
    sharpe_ls_net_f = float(sharpe_ls_net)
    gate3 = bool(not math.isnan(sharpe_ls_net_f) and sharpe_ls_net_f >= 1.4)

    return {
        "gate1_subperiod_improvement": gate1,
        "gate1_periods_passing": int(gate1_periods_passing),
        "gate1_detail": gate1_detail,
        "gate2_greenspan_ok": gate2,
        "gate2_concentration_ratio": concentration,
        "gate3_sharpe_net_ok": gate3,
        "gate3_sharpe_net": sharpe_ls_net_f,
        "all_gates_pass": bool(gate1 and gate2 and gate3),
        "bias_note": _BIAS_NOTE,
    }


# ---------------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------------


def _plot_ls_vs_lo(
    equity_ls: pd.Series,
    equity_lo: pd.Series,
    out_path: Path,
) -> None:
    growth_ls = (1 + equity_ls).cumprod()
    growth_lo = (1 + equity_lo).cumprod()

    idx = pd.to_datetime(equity_ls.index.tolist())
    idx_lo = pd.to_datetime(equity_lo.index.tolist())

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(idx, growth_ls.values, label="TSMOM L/S", linewidth=1.2)
    ax.plot(
        idx_lo, growth_lo.values, label="TSMOM Long-Only", linewidth=1.2, linestyle="--"
    )
    ax.set_title("TSMOM IS: Long/Short vs Long-Only (base = 1.0)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Growth Factor")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# JSON serialisation helper
# ---------------------------------------------------------------------------


def _fmt(v: float) -> float | None:
    return None if math.isnan(v) else v


def _fmt_gates(gates: dict) -> dict:
    """Prepare gates dict for JSON: replace NaN floats with None."""
    result = dict(gates)
    result["gate1_detail"] = {
        k: _fmt(v) for k, v in gates["gate1_detail"].items()
    }
    result["gate2_concentration_ratio"] = _fmt(gates["gate2_concentration_ratio"])
    result["gate3_sharpe_net"] = _fmt(gates["gate3_sharpe_net"])
    return result


# ---------------------------------------------------------------------------
# Main execution block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # --- 1. Run L/S and long-only backtests ---
    equity_ls, _ = run_backtest_ls(
        _TICKERS, _IS_START, _IS_END, _BORROW_COSTS, _LOOKBACK_MONTHS, _TARGET_VOL
    )
    equity_lo, _ = run_backtest_range(
        _TICKERS, _IS_START, _IS_END, _LOOKBACK_MONTHS, _TARGET_VOL
    )

    # --- 2. Apply warmup trim + drop trailing NaN from open.shift(-1) ---
    warmup = _LOOKBACK_MONTHS * 21
    equity_ls_trimmed = equity_ls.iloc[warmup:].dropna()
    equity_lo_trimmed = equity_lo.iloc[warmup:].dropna()

    # --- 3. Sub-period Sharpes ---
    sharpe_ls = compute_subperiod_sharpes(equity_ls_trimmed, _SUB_PERIODS)
    sharpe_lo = compute_subperiod_sharpes(equity_lo_trimmed, _SUB_PERIODS)

    # --- 4. Full-period metrics ---
    report_ls = compute_performance(equity_ls_trimmed)
    sharpe_ls_net = report_ls.sharpe

    # --- 5. Evaluate gates ---
    gates = evaluate_is_gates(sharpe_ls, sharpe_lo, sharpe_ls_net)

    # --- 6. Rebalance count (unique months in trimmed equity) ---
    idx_dt = pd.to_datetime([str(d) for d in equity_ls_trimmed.index])
    n_rebalances = int(idx_dt.to_period("M").nunique())

    # --- 7. Persist results ---
    out_dir = Path("results/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y%m%d")
    stem = f"tsmom_ls_v4_{today}"

    equity_ls_trimmed.to_csv(out_dir / f"{stem}.csv", header=True)

    metrics: dict = {
        "sharpe_ls_net": _fmt(sharpe_ls_net),
        "max_drawdown_ls": _fmt(report_ls.max_drawdown),
        "n_rebalances": n_rebalances,
        "subperiod_sharpes_ls": {k: _fmt(v) for k, v in sharpe_ls.items()},
        "subperiod_sharpes_lo": {k: _fmt(v) for k, v in sharpe_lo.items()},
        **_fmt_gates(gates),
    }
    with open(out_dir / f"{stem}_metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    _plot_ls_vs_lo(equity_ls_trimmed, equity_lo_trimmed, out_dir / f"{stem}.png")

    # --- 8. Stdout: comparison table + gate results ---
    print("\nSub-period Sharpe comparison (L/S vs Long-Only):")
    print(f"{'Period':<10}  {'sharpe_ls':>10}  {'sharpe_lo':>10}  {'delta':>8}")
    print("-" * 46)
    for k in _SUB_PERIOD_KEYS:
        ls_v = sharpe_ls.get(k, float("nan"))
        lo_v = sharpe_lo.get(k, float("nan"))
        delta = ls_v - lo_v
        ls_str = f"{ls_v:10.3f}" if not math.isnan(ls_v) else f"{'nan':>10}"
        lo_str = f"{lo_v:10.3f}" if not math.isnan(lo_v) else f"{'nan':>10}"
        d_str = f"{delta:8.3f}" if not math.isnan(delta) else f"{'nan':>8}"
        print(f"{k:<10}  {ls_str}  {lo_str}  {d_str}")

    print()
    g1_ok = "PASS" if gates["gate1_subperiod_improvement"] else "FAIL"
    g2_ok = "PASS" if gates["gate2_greenspan_ok"] else "FAIL"
    g3_ok = "PASS" if gates["gate3_sharpe_net_ok"] else "FAIL"
    conc = gates["gate2_concentration_ratio"]
    conc_str = f"{conc:.3f}" if not math.isnan(conc) else "nan"
    n_pass = gates["gate1_periods_passing"]
    print(f"Gate 1 (≥3 of 4 sub-periods δ ≥ 0.15):  {g1_ok}  ({n_pass}/4 passing)")
    print(f"Gate 2 (Greenspan concentration < 0.50): {g2_ok}  (ratio = {conc_str})")
    print(f"Gate 3 (Sharpe net ≥ 1.40):              {g3_ok}"
          f"  (sharpe = {sharpe_ls_net:.3f})")
    print()
    verdict = "PASS" if gates["all_gates_pass"] else "FAIL"
    print(f"Verdict: all_gates_pass = {gates['all_gates_pass']}  [{verdict}]")
    print(f"\nResults saved to: {out_dir / stem}.*")
