"""
scripts/run_is_v5.py
--------------------
IS runner: TSMOM long/short v5 on SPY+TLT+GLD+FXE+FXY with explicit borrow costs.

Evaluates 3 pre-registered IS gates and compares L/S vs long-only by sub-period.
Includes informational FX concentration diagnostic for FXE+FXY.
The if __name__ == "__main__" block requires network access; helpers are pure/mockable.

ADR: FXY IS 2014-2021 distorted by BOJ YCC. Signals may reflect policy-driven momentum.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from run_is import compute_asset_attribution, compute_subperiod_sharpes

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.backtest.runner import (
    _compute_ewma_vol_weight,
    _compute_monthly_signal,
    _pivot_ohlcv,
    run_backtest_ls,
    run_backtest_range,
)
from trading.domain.metrics.equity_metrics import PerformanceReport, compute_performance

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_TICKERS = ["SPY", "TLT", "GLD", "FXE", "FXY"]
_LOOKBACK_MONTHS = 12
_TARGET_VOL = 0.10
_IS_START = date(2005, 1, 1)
_IS_END = date(2021, 12, 31)
_BORROW_COSTS: dict[str, float] = {
    "SPY": 0.0,
    "TLT": 0.001,
    "GLD": 0.004,
    "FXE": 0.0,
    "FXY": 0.0,
}

_SUB_PERIODS = [
    ("2005-01-01", "2008-12-31"),
    ("2009-01-01", "2013-12-31"),
    ("2014-01-01", "2018-12-31"),
    ("2019-01-01", "2021-12-31"),
]

_SUB_PERIOD_KEYS = ["2005-08", "2009-13", "2014-18", "2019-21"]

_YCC_BIAS_NOTE = (
    "FXY IS 2014-2021: BOJ Yield Curve Control artificially suppressed JPY trends. "
    "Signals in sub-periods 2014-18 and 2019-21 may reflect policy-driven momentum, "
    "not free-market price discovery. BOJ normalization began 2024"
    " — OOS regime differs."
)


# ---------------------------------------------------------------------------
# Helper 1: per-asset L/S daily return attribution
# ---------------------------------------------------------------------------


def compute_ls_asset_attribution(
    tickers: list[str],
    start: date,
    end: date,
    borrow_costs: dict[str, float],
    lookback_months: int,
    target_vol: float,
) -> pd.DataFrame:
    """
    Compute per-asset daily return contribution for the L/S strategy.

    Replicates run_backtest_ls signal + sizing logic without averaging across tickers,
    returning a per-column view of contributions.

    Borrow cost drag is applied only to short positions, identical to run_backtest_ls.

    Parameters
    ----------
    tickers : list[str]
        Asset universe.
    start, end : date
        Date range.
    borrow_costs : dict[str, float]
        Annualised borrow rate per ticker. Absent ticker → 0.0.
    lookback_months : int
        Momentum lookback in months.
    target_vol : float
        Annualised per-asset volatility target.

    Returns
    -------
    pd.DataFrame
        Columns = tickers, index = datetime.date, values = daily contribution per asset
        (before averaging). NaN propagates without fillna.
    """
    data = YFinanceAdapter().load_ohlcv_daily(tickers, start, end)
    close = _pivot_ohlcv(data, "close")
    open_ = _pivot_ohlcv(data, "open")

    lookback_days = lookback_months * 21
    signal_lo = _compute_monthly_signal(close, lookback_days)
    signal_ls = signal_lo.replace(0, -1.0)

    entry_price = open_.shift(-1)
    daily_asset_returns = entry_price.shift(-1) / entry_price - 1.0

    vol_weight = _compute_ewma_vol_weight(close, target_vol)

    borrow_per_ticker = pd.Series(
        {t: borrow_costs.get(t, 0.0) for t in signal_ls.columns}
    )
    borrow_drag = (
        (signal_ls == -1).astype(float) * (borrow_per_ticker / 252.0) * vol_weight
    )

    asset_pnl = signal_ls * daily_asset_returns * vol_weight - borrow_drag
    asset_pnl.index = asset_pnl.index.date
    return asset_pnl


# ---------------------------------------------------------------------------
# Helper 2: Greenspan concentration ratio (operates on pre-computed deltas)
# ---------------------------------------------------------------------------


def compute_greenspan_concentration(
    subperiod_deltas: dict[str, float],
) -> float | None:
    """
    Compute the Greenspan concentration ratio from pre-computed sub-period deltas.

    Concentration = delta["2005-08"] / sum(all 4 deltas).

    Parameters
    ----------
    subperiod_deltas : dict[str, float]
        Delta per sub-period, keyed by _SUB_PERIOD_KEYS (e.g. "2005-08").
        Values are typically sharpe_ls[k] - sharpe_lo[k].

    Returns
    -------
    float | None
        Concentration ratio ∈ (0, 1], or None if:
        - Any value is NaN
        - Sum of all 4 deltas ≤ 0
    """
    vals: list[float] = []
    for k in _SUB_PERIOD_KEYS:
        v = subperiod_deltas.get(k, float("nan"))
        if math.isnan(v):
            return None
        vals.append(v)

    total = sum(vals)
    if total <= 0:
        return None

    return vals[0] / total  # vals[0] = delta["2005-08"]


# ---------------------------------------------------------------------------
# Helper 3: FX concentration diagnostic (informational, not a gate)
# ---------------------------------------------------------------------------


def compute_fx_concentration(
    subperiod_deltas: dict[str, float],
    attribution_ls: pd.DataFrame,
    attribution_lo: pd.DataFrame,
    sub_periods: list[tuple[str, str]],
) -> dict:
    """
    Compute the incremental FX delta (FXE+FXY) attributable per sub-period.

    Measures how concentrated the FX contribution is across sub-periods.
    This is an informational diagnostic — NOT a gate for strategy rejection.

    Delta per sub-period:
        fx_delta[sp] = (mean_daily_FX_LS - mean_daily_FX_LO) × n_trading_days[sp]

    Parameters
    ----------
    subperiod_deltas : dict[str, float]
        Pre-computed Sharpe deltas per sub-period (for reference/context).
    attribution_ls : pd.DataFrame
        Per-asset daily returns for L/S strategy. Must contain FXE and/or FXY.
    attribution_lo : pd.DataFrame
        Per-asset daily returns for long-only strategy.
    sub_periods : list of (start_iso, end_iso) tuples.

    Returns
    -------
    dict with keys:
        subperiod_max_fx_share : float
            Maximum FX share across all sub-periods (fraction of total abs FX delta).
        subperiod_max_fx_label : str
            Key of the sub-period with the largest FX share.
        concentrated : bool
            True if any sub-period's FX contribution > 50% of total abs FX delta.
    """
    fx_tickers_ls = [t for t in ["FXE", "FXY"] if t in attribution_ls.columns]
    fx_tickers_lo = [t for t in ["FXE", "FXY"] if t in attribution_lo.columns]

    fx_deltas: dict[str, float] = {}
    for start_iso, end_iso in sub_periods:
        key = f"{start_iso[:4]}-{end_iso[2:4]}"
        start_d = date.fromisoformat(start_iso)
        end_d = date.fromisoformat(end_iso)

        mask_ls = (attribution_ls.index >= start_d) & (attribution_ls.index <= end_d)
        mask_lo = (attribution_lo.index >= start_d) & (attribution_lo.index <= end_d)

        n_days = int(mask_ls.sum())
        if n_days == 0:
            fx_deltas[key] = 0.0
            continue

        ls_fx_mean = (
            attribution_ls.loc[mask_ls, fx_tickers_ls].sum(axis=1).mean()
            if fx_tickers_ls
            else 0.0
        )
        lo_fx_mean = (
            attribution_lo.loc[mask_lo, fx_tickers_lo].sum(axis=1).mean()
            if fx_tickers_lo and mask_lo.sum() > 0
            else 0.0
        )

        fx_deltas[key] = (ls_fx_mean - lo_fx_mean) * n_days

    total_abs = sum(abs(v) for v in fx_deltas.values())
    if total_abs == 0.0:
        return {
            "subperiod_max_fx_share": 0.0,
            "subperiod_max_fx_label": "",
            "concentrated": False,
        }

    max_share = 0.0
    max_label = ""
    for k, v in fx_deltas.items():
        share = abs(v) / total_abs
        if share > max_share:
            max_share = share
            max_label = k

    return {
        "subperiod_max_fx_share": max_share,
        "subperiod_max_fx_label": max_label,
        "concentrated": bool(max_share > 0.50),
    }


# ---------------------------------------------------------------------------
# Helper 4: IS gate evaluation v5
# ---------------------------------------------------------------------------


def evaluate_is_gates_v5(
    ls_sharpes: dict[str, float],
    lo_sharpes: dict[str, float],
    ls_sharpe_total: float,
) -> dict:
    """
    Evaluate the 3 pre-registered IS gates for the L/S v5 strategy.

    Gate 1: ≥ 3 of 4 sub-periods with delta(sharpe_ls - sharpe_lo) ≥ 0.15
    Gate 2: Greenspan concentration ratio < 0.50
    Gate 3: IS Sharpe ≥ 1.40

    Parameters
    ----------
    ls_sharpes : dict[str, float]
        Sub-period Sharpe ratios for L/S, keyed by _SUB_PERIOD_KEYS.
    lo_sharpes : dict[str, float]
        Sub-period Sharpe ratios for long-only, keyed by _SUB_PERIOD_KEYS.
    ls_sharpe_total : float
        Full-period IS Sharpe ratio for the L/S strategy.

    Returns
    -------
    dict with keys:
        gate1_pass, gate1_count, gate1_deltas,
        gate2_pass, gate2_concentration,
        gate3_pass, gate3_sharpe,
        all_gates_pass.
    """
    # Gate 1: sub-period improvement
    gate1_deltas: dict[str, float] = {}
    for k in _SUB_PERIOD_KEYS:
        ls_v = ls_sharpes.get(k, float("nan"))
        lo_v = lo_sharpes.get(k, float("nan"))
        gate1_deltas[k] = ls_v - lo_v

    gate1_count = sum(
        1
        for d in gate1_deltas.values()
        if not math.isnan(d) and d >= 0.15
    )
    gate1_pass = bool(gate1_count >= 3)

    # Gate 2: Greenspan concentration (uses pre-computed deltas)
    concentration = compute_greenspan_concentration(gate1_deltas)
    gate2_pass = bool(concentration is not None and concentration < 0.50)

    # Gate 3: total IS Sharpe
    gate3_sharpe = float(ls_sharpe_total)
    gate3_pass = bool(not math.isnan(gate3_sharpe) and gate3_sharpe >= 1.4)

    return {
        "gate1_pass": gate1_pass,
        "gate1_count": int(gate1_count),
        "gate1_deltas": gate1_deltas,
        "gate2_pass": gate2_pass,
        "gate2_concentration": concentration,
        "gate3_pass": gate3_pass,
        "gate3_sharpe": gate3_sharpe,
        "all_gates_pass": bool(gate1_pass and gate2_pass and gate3_pass),
    }


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _fmt(v: float) -> float | None:
    return None if math.isnan(v) else v


def _make_metrics(
    report_ls: PerformanceReport,
    report_lo: PerformanceReport,
    ls_sharpes: dict[str, float],
    lo_sharpes: dict[str, float],
    gates: dict,
    fx_diag: dict,
    n_rebalances: int,
    borrow_costs: dict[str, float],
) -> dict:
    subperiods_out = {}
    for k in _SUB_PERIOD_KEYS:
        subperiods_out[k] = {
            "sharpe_ls": _fmt(ls_sharpes.get(k, float("nan"))),
            "sharpe_lo": _fmt(lo_sharpes.get(k, float("nan"))),
            "delta": _fmt(gates["gate1_deltas"].get(k, float("nan"))),
        }

    return {
        "sharpe_ls": _fmt(report_ls.sharpe),
        "sharpe_lo": _fmt(report_lo.sharpe),
        "max_drawdown_ls": _fmt(report_ls.max_drawdown),
        "n_rebalances": n_rebalances,
        "subperiods": subperiods_out,
        "gate1_pass": gates["gate1_pass"],
        "gate1_count": gates["gate1_count"],
        "gate2_pass": gates["gate2_pass"],
        "gate2_concentration": gates["gate2_concentration"],
        "gate3_pass": gates["gate3_pass"],
        "gate3_sharpe": _fmt(gates["gate3_sharpe"]),
        "all_gates_pass": gates["all_gates_pass"],
        "bias_note": _YCC_BIAS_NOTE,
        "fx_concentration_diagnostic": fx_diag,
        "borrow_costs": borrow_costs,
    }


# ---------------------------------------------------------------------------
# Plot helper (3 subplots: equity LS, equity LO, drawdown LS)
# ---------------------------------------------------------------------------


def _plot_equity_drawdown(
    equity_ls: pd.Series,
    equity_lo: pd.Series,
    out_path: Path,
) -> None:
    growth_ls = (1 + equity_ls).cumprod()
    growth_lo = (1 + equity_lo).cumprod()
    dd_ls = (growth_ls / growth_ls.cummax() - 1) * 100

    idx_ls = pd.to_datetime(equity_ls.index.tolist())
    idx_lo = pd.to_datetime(equity_lo.index.tolist())

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 11), sharex=False)

    ax1.plot(idx_ls, growth_ls.values, linewidth=1.2, color="steelblue")
    ax1.set_title("TSMOM L/S IS Equity (SPY+TLT+GLD+FXE+FXY)")
    ax1.set_ylabel("Growth Factor")
    ax1.grid(True, alpha=0.3)

    ax2.plot(
        idx_lo, growth_lo.values, linewidth=1.2, color="darkorange", linestyle="--"
    )
    ax2.set_title("TSMOM Long-Only IS Equity (same universe)")
    ax2.set_ylabel("Growth Factor")
    ax2.grid(True, alpha=0.3)

    ax3.fill_between(idx_ls, dd_ls.values, 0, color="red", alpha=0.4)
    ax3.set_title("L/S Drawdown (%)")
    ax3.set_ylabel("Drawdown (%)")
    ax3.set_xlabel("Date")
    ax3.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


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

    # --- 2. Warmup trim + drop trailing NaN from open.shift(-1) ---
    warmup = _LOOKBACK_MONTHS * 21
    equity_ls_trimmed = equity_ls.iloc[warmup:].dropna()
    equity_lo_trimmed = equity_lo.iloc[warmup:].dropna()

    # --- 3. Sub-period Sharpes ---
    ls_sharpes = compute_subperiod_sharpes(equity_ls_trimmed, _SUB_PERIODS)
    lo_sharpes = compute_subperiod_sharpes(equity_lo_trimmed, _SUB_PERIODS)

    # --- 4. Full-period metrics ---
    report_ls = compute_performance(equity_ls_trimmed)
    report_lo = compute_performance(equity_lo_trimmed)

    # --- 5. Evaluate gates ---
    gates = evaluate_is_gates_v5(ls_sharpes, lo_sharpes, report_ls.sharpe)

    # --- 6. Asset attribution ---
    attribution_ls = compute_ls_asset_attribution(
        _TICKERS, _IS_START, _IS_END, _BORROW_COSTS, _LOOKBACK_MONTHS, _TARGET_VOL
    )
    attribution_lo, n_rebalances = compute_asset_attribution(
        _TICKERS, _IS_START, _IS_END, _LOOKBACK_MONTHS, _TARGET_VOL
    )

    # --- 7. FX concentration diagnostic ---
    fx_diag = compute_fx_concentration(
        gates["gate1_deltas"], attribution_ls, attribution_lo, _SUB_PERIODS
    )

    # --- 8. Persist results ---
    out_dir = Path("results/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y%m%d")
    stem = f"tsmom_ls_v5_{today}"

    equity_ls_trimmed.to_csv(out_dir / f"{stem}.csv", header=True)

    metrics = _make_metrics(
        report_ls=report_ls,
        report_lo=report_lo,
        ls_sharpes=ls_sharpes,
        lo_sharpes=lo_sharpes,
        gates=gates,
        fx_diag=fx_diag,
        n_rebalances=n_rebalances,
        borrow_costs=_BORROW_COSTS,
    )
    with open(out_dir / f"{stem}_metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    _plot_equity_drawdown(
        equity_ls_trimmed, equity_lo_trimmed, out_dir / f"{stem}.png"
    )

    # --- 9. Stdout: comparison table + gate results ---
    print("\nSub-period Sharpe comparison (L/S vs Long-Only):")
    print(
        f"{'Period':<10}  {'sharpe_ls':>10}  {'sharpe_lo':>10}"
        f"  {'delta':>8}  {'gate1':>6}"
    )
    print("-" * 56)
    for k in _SUB_PERIOD_KEYS:
        ls_v = ls_sharpes.get(k, float("nan"))
        lo_v = lo_sharpes.get(k, float("nan"))
        delta = ls_v - lo_v
        g1_cell = "PASS" if (not math.isnan(delta) and delta >= 0.15) else "FAIL"
        ls_str = f"{ls_v:10.3f}" if not math.isnan(ls_v) else f"{'nan':>10}"
        lo_str = f"{lo_v:10.3f}" if not math.isnan(lo_v) else f"{'nan':>10}"
        d_str = f"{delta:8.3f}" if not math.isnan(delta) else f"{'nan':>8}"
        print(f"{k:<10}  {ls_str}  {lo_str}  {d_str}  {g1_cell:>6}")

    print()
    g1_ok = "PASS" if gates["gate1_pass"] else "FAIL"
    g2_ok = "PASS" if gates["gate2_pass"] else "FAIL"
    g3_ok = "PASS" if gates["gate3_pass"] else "FAIL"
    conc = gates["gate2_concentration"]
    conc_str = f"{conc:.3f}" if conc is not None else "None"
    n_pass = gates["gate1_count"]
    print(f"Gate 1 (≥3 of 4 sub-periods δ ≥ 0.15):  {g1_ok}  ({n_pass}/4 passing)")
    print(f"Gate 2 (Greenspan concentration < 0.50): {g2_ok}  (ratio = {conc_str})")
    sharpe_str = f"{report_ls.sharpe:.3f}"
    print(f"Gate 3 (Sharpe ≥ 1.40):                 {g3_ok}  (sharpe = {sharpe_str})")
    print()
    verdict = "PASS" if gates["all_gates_pass"] else "FAIL"
    print(f"Verdict: all_gates_pass = {gates['all_gates_pass']}  [{verdict}]")
    print(f"\nFX diagnostic: max FX share = {fx_diag['subperiod_max_fx_share']:.1%}"
          f" ({fx_diag['subperiod_max_fx_label']})"
          f" concentrated={fx_diag['concentrated']}")
    print(f"Bias note: {_YCC_BIAS_NOTE}")
    print(f"\nResults saved to: {out_dir / stem}.*")
