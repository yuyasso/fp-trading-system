"""
scripts/run_wf.py
-----------------
Walk-forward expanding window evaluation of the TSMOM strategy.

OOS period: 2022Q1 – 2026Q1 (quarterly, expanding IS window from 2005-01-01).
Regime detection: FFR-based (FOMC hardcoded dict).
Statistical context: stationary block bootstrap Sharpe CI (Politis-Romano).
Gate evaluation: pre-registered gates per regime (normal / stress).

Usage:
    python scripts/run_wf.py

Outputs (results/backtest/):
    tsmom_wf_YYYYMMDD.csv
    tsmom_wf_YYYYMMDD_summary.json
    tsmom_wf_YYYYMMDD.png
"""
from __future__ import annotations

import calendar
import json
import logging
import math
from datetime import date, timedelta
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.backtest.runner import run_backtest_range
from trading.domain.metrics.equity_metrics import Frequency, compute_performance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — NOT free parameters
# ---------------------------------------------------------------------------

_IS_START = date(2005, 1, 1)
_OOS_START = date(2022, 1, 1)
_OOS_END = date(2026, 4, 30)  # last complete month with available data
_LOOKBACK_MONTHS = 12
_TARGET_VOL = 0.10
_TICKERS = ["SPY", "TLT", "GLD", "DBC", "UUP"]
_STRESS_FFR_THRESHOLD = 4.0  # FFR > 4% → stress regime
_STRESS_MIN_FRACTION = 0.40  # ≥40% of business days in quarter must be stress
_BOOTSTRAP_N = 1000
_BOOTSTRAP_BLOCK = 21  # ~1 month of business days

_STATISTICAL_WARNING = (
    "~63 obs/trimestre; intervalos bootstrap amplios. "
    "No inferir significancia de ventanas individuales."
)

# ---------------------------------------------------------------------------
# FOMC rate decisions — hardcoded, NOT a free parameter
# Source: https://www.federalreserve.gov/monetarypolicy/openmarket.htm
#         and individual press releases at federalreserve.gov/newsevents/pressreleases/
# Format: {decision_date: FFR_upper_bound_percent}
# Gaps between decisions are handled by forward-fill in get_ffr_on_date().
# ---------------------------------------------------------------------------

_FOMC_RATES: dict[date, float] = {
    # Ciclo Greenspan — hiking 2004-2006
    date(2004, 6, 30): 1.25,
    date(2004, 8, 10): 1.50,
    date(2004, 9, 21): 1.75,
    date(2004, 11, 10): 2.00,
    date(2004, 12, 14): 2.25,
    date(2005, 2, 2): 2.50,
    date(2005, 3, 22): 2.75,
    date(2005, 5, 3): 3.00,
    date(2005, 6, 30): 3.25,
    date(2005, 8, 9): 3.50,
    date(2005, 9, 20): 3.75,
    date(2005, 11, 1): 4.00,
    date(2005, 12, 13): 4.25,
    date(2006, 1, 31): 4.50,
    date(2006, 3, 28): 4.75,
    date(2006, 5, 10): 5.00,
    date(2006, 6, 29): 5.25,
    # Easing post-Greenspan + crisis 2007-2008
    date(2007, 9, 18): 4.75,
    date(2007, 10, 31): 4.50,
    date(2007, 12, 11): 4.25,
    date(2008, 1, 22): 3.50,
    date(2008, 1, 30): 3.00,
    date(2008, 3, 18): 2.25,
    date(2008, 4, 30): 2.00,
    date(2008, 10, 8): 1.50,
    date(2008, 10, 29): 1.00,
    date(2008, 12, 16): 0.25,
    # ZLB placeholder — not a real FOMC decision date; used for IS FFR lookups only
    date(2009, 1, 1): 0.25,
    # Ciclo Yellen — hiking 2015-2018
    date(2015, 12, 16): 0.50,
    date(2016, 12, 14): 0.75,
    date(2017, 3, 15): 1.00,
    date(2017, 6, 14): 1.25,
    date(2017, 12, 13): 1.50,
    date(2018, 3, 21): 1.75,
    date(2018, 6, 13): 2.00,
    date(2018, 9, 26): 2.25,
    date(2018, 12, 19): 2.50,
    # Easing Powell 2019 + COVID 2020
    date(2019, 7, 31): 2.25,
    date(2019, 9, 18): 2.00,
    date(2019, 10, 30): 1.75,
    date(2020, 3, 3): 1.25,
    date(2020, 3, 15): 0.25,
    # 2022: hiking cycle begins
    date(2022, 3, 17): 0.50,
    date(2022, 5, 5): 1.00,
    date(2022, 6, 16): 1.75,
    date(2022, 7, 28): 2.50,
    date(2022, 9, 22): 3.25,
    date(2022, 11, 3): 4.00,
    date(2022, 12, 15): 4.50,
    # 2023: further hikes to 5.50%, then hold
    date(2023, 2, 2): 4.75,
    date(2023, 3, 23): 5.00,
    date(2023, 5, 4): 5.25,
    date(2023, 7, 27): 5.50,
    # 2024: hold then easing cycle begins
    date(2024, 9, 19): 5.00,
    date(2024, 11, 7): 4.75,
    date(2024, 12, 19): 4.50,
    # 2025: hold then further cuts
    date(2025, 1, 29): 4.50,
    date(2025, 3, 19): 4.50,
    date(2025, 5, 7): 4.50,
    date(2025, 6, 18): 4.50,
    date(2025, 7, 30): 4.50,
    date(2025, 9, 17): 4.25,
    date(2025, 10, 29): 4.00,
    date(2025, 12, 10): 3.75,
    # 2026: hold at 3.50-3.75%
    date(2026, 1, 28): 3.75,
    date(2026, 3, 18): 3.75,
    date(2026, 4, 29): 3.75,
}


# ---------------------------------------------------------------------------
# Testable helpers
# ---------------------------------------------------------------------------


def get_ffr_on_date(d: date, fomc_rates: dict[date, float]) -> float:
    """
    Return the effective FFR on date ``d`` by forward-filling the last
    FOMC decision on or before ``d``.

    Parameters
    ----------
    d : date
        Target date.
    fomc_rates : dict[date, float]
        Mapping of FOMC decision date → FFR upper bound (%).

    Returns
    -------
    float
        FFR upper bound in percent. Returns 0.0 if ``d`` is before all
        entries in the dict (pre-hike era treated as zero rate).
    """
    candidates = [k for k in fomc_rates if k <= d]
    if not candidates:
        return 0.0
    return fomc_rates[max(candidates)]


def classify_quarter_regime(
    oos_start: date,
    oos_end: date,
    fomc_rates: dict[date, float],
) -> str:
    """
    Classify a quarter as ``"stress"`` or ``"normal"`` based on the fraction
    of business days where FFR > _STRESS_FFR_THRESHOLD.

    Business days: ``[oos_start, oos_end)`` (oos_end exclusive).

    Returns ``"stress"`` if ≥ _STRESS_MIN_FRACTION of days have FFR above
    the threshold; ``"normal"`` otherwise.
    """
    end_inclusive = oos_end - timedelta(days=1)
    bdays = list(pd.bdate_range(oos_start, end_inclusive).date)
    if not bdays:
        return "normal"
    stress_count = sum(
        1 for d in bdays if get_ffr_on_date(d, fomc_rates) > _STRESS_FFR_THRESHOLD
    )
    if stress_count / len(bdays) >= _STRESS_MIN_FRACTION:
        return "stress"
    return "normal"


def generate_wf_windows(oos_start: date, oos_end: date) -> list[dict]:
    """
    Generate non-overlapping quarterly expanding-window definitions.

    Each window dict:
        is_start  : always _IS_START
        is_end    : oos_start - 1 day (last IS day)
        oos_start : first day of the quarter
        oos_end   : last day of the quarter (or oos_end if partial)

    Quarters: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec.
    The last window may be partial (oos_end = min(quarter_end, oos_end)).
    """
    windows = []
    current = oos_start

    while current < oos_end:
        # Determine the end of the current quarter
        q_idx = (current.month - 1) // 3  # 0-based quarter index
        q_end_month = (q_idx + 1) * 3  # last month of the quarter
        q_end_day = calendar.monthrange(current.year, q_end_month)[1]
        quarter_end = date(current.year, q_end_month, q_end_day)

        window_oos_end = min(quarter_end, oos_end)
        windows.append(
            {
                "is_start": _IS_START,
                "is_end": current - timedelta(days=1),
                "oos_start": current,
                "oos_end": window_oos_end,
            }
        )

        if window_oos_end >= oos_end:
            break

        # Advance to the first day of the next quarter
        if q_end_month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, q_end_month + 1, 1)

    return windows


def stationary_block_bootstrap_sharpe_ci(
    returns: pd.Series,
    n_bootstrap: int = 1000,
    block_size: int = 21,
) -> tuple[float, float]:
    """
    Politis-Romano stationary block bootstrap 95% confidence interval for
    the annualized Sharpe ratio.

    Block lengths are drawn from Geometric(1/block_size); blocks are sampled
    with circular wrap-around until len(returns) observations are collected.
    Sharpe = mean(r) / std(r, ddof=1) * sqrt(252) for each replicate.

    Returns
    -------
    (ci_lo, ci_hi) : tuple[float, float]
        2.5th and 97.5th percentiles of the bootstrap distribution.
        Returns (nan, nan) if len(returns) < 2 * block_size.
    """
    n = len(returns)
    if n < 2 * block_size:
        return (float("nan"), float("nan"))

    arr = returns.to_numpy(dtype=float)
    rng = np.random.default_rng(42)
    sharpes: list[float] = []

    for _ in range(n_bootstrap):
        sample: list[float] = []
        while len(sample) < n:
            # Geometric block length (minimum 1)
            block_len = int(rng.geometric(1.0 / block_size))
            block_len = min(block_len, n - len(sample))
            start_idx = int(rng.integers(0, n))
            # Circular wrap-around
            indices = [(start_idx + i) % n for i in range(block_len)]
            sample.extend(arr[indices])

        boot = np.array(sample[:n])
        std = float(boot.std(ddof=1))
        if std < 1e-12:
            continue
        sharpes.append(float(boot.mean() / std * math.sqrt(252)))

    if len(sharpes) < 2:
        return (float("nan"), float("nan"))

    arr_s = np.array(sharpes)
    return (float(np.percentile(arr_s, 2.5)), float(np.percentile(arr_s, 97.5)))


def evaluate_gates(
    sharpe_oos: float,
    max_dd_oos: float,
    sharpe_is: float,
    regime: str,
) -> dict:
    """
    Evaluate pre-registered performance gates for a single OOS window.

    Gates (immutable — PO pre-registered):
        Normal regime : sharpe_oos > 0.8 AND max_dd_oos < 0.15
                        AND ratio_oos_is > 0.35
        Stress regime : sharpe_oos > -0.5 AND max_dd_oos < 0.20
        Stop absolute : regime == "normal" AND sharpe_oos < 0.3

    Parameters
    ----------
    sharpe_oos : float
        OOS annualized Sharpe ratio.
    max_dd_oos : float
        OOS maximum drawdown magnitude (positive, e.g. 0.12 for 12%).
    sharpe_is : float
        IS annualized Sharpe ratio (used for ratio gate).
    regime : str
        ``"normal"`` or ``"stress"``.

    Returns
    -------
    dict with keys: regime, sharpe_oos, max_dd_oos, ratio_oos_is,
                    gate_pass, stop_triggered, detail.
    """
    dd = abs(max_dd_oos)

    if sharpe_is != 0 and not (math.isnan(sharpe_is) or math.isinf(sharpe_is)):
        ratio_oos_is = sharpe_oos / sharpe_is
    else:
        ratio_oos_is = float("nan")

    if regime == "normal":
        stop_triggered = sharpe_oos < 0.3
        ratio_ok = (not math.isnan(ratio_oos_is)) and ratio_oos_is > 0.35
        gate_pass = sharpe_oos > 0.8 and dd < 0.15 and ratio_ok
        if gate_pass:
            detail = "All normal-regime gates passed."
        elif stop_triggered:
            detail = (
                f"STOP triggered: Sharpe OOS {sharpe_oos:.3f} < 0.3 "
                "in normal regime."
            )
        else:
            detail = (
                f"Normal gates failed: Sharpe={sharpe_oos:.3f} (need >0.8), "
                f"DD={dd:.3f} (need <0.15), "
                f"Ratio={ratio_oos_is:.3f} (need >0.35)."
            )
    else:  # stress
        stop_triggered = False
        gate_pass = sharpe_oos > -0.5 and dd < 0.20
        if gate_pass:
            detail = "All stress-regime gates passed."
        else:
            detail = (
                f"Stress gates failed: Sharpe={sharpe_oos:.3f} (need >-0.5), "
                f"DD={dd:.3f} (need <0.20)."
            )

    return {
        "regime": regime,
        "sharpe_oos": sharpe_oos,
        "max_dd_oos": max_dd_oos,
        "ratio_oos_is": ratio_oos_is,
        "gate_pass": gate_pass,
        "stop_triggered": stop_triggered,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Diagnostic: 2022Q2 momentum signal check
# ---------------------------------------------------------------------------


def compute_diagnostic_2022q2(close_prices: pd.DataFrame) -> dict:
    """
    Post-hoc check: which tickers had a positive 12m momentum signal at start of 2022Q2.

    Parameters
    ----------
    close_prices : pd.DataFrame
        DataFrame with ``datetime.date`` index and ticker columns.
        Must cover at least 2021-04-01 to 2022-04-01.

    Returns
    -------
    dict with keys:
        tickers_with_positive_signal : list[str]  (may be empty, never null)
        note                         : str
    """
    target = date(2022, 4, 1)
    lookback = date(2021, 4, 1)

    tickers_positive: list[str] = []
    for ticker in close_prices.columns:
        col = close_prices[ticker].dropna()
        idx = col.index.tolist()

        now_candidates = [d for d in idx if d >= target]
        then_candidates = [d for d in idx if d >= lookback]
        if not now_candidates or not then_candidates:
            continue

        price_now = float(col.loc[now_candidates[0]])
        price_then = float(col.loc[then_candidates[0]])
        if price_then > 0 and (price_now / price_then - 1) > 0:
            tickers_positive.append(ticker)

    return {
        "tickers_with_positive_signal": sorted(tickers_positive),
        "note": (
            "Post-hoc check: tickers with positive 12m momentum signal"
            " at start of 2022Q2"
        ),
    }


# ---------------------------------------------------------------------------
# Internal plotting helper
# ---------------------------------------------------------------------------


def _quarter_label(d: date) -> str:
    """Format a quarter-start date as 'YYYYQn' label."""
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


def _plot_wf_results(df: pd.DataFrame, output_path: Path) -> None:
    """
    Generate 3-subplot walk-forward results chart.

    Subplots (sharex=True):
        Top    : Sharpe OOS by quarter (bars + bootstrap CI error bars)
        Middle : Max drawdown OOS by quarter (bars)
        Bottom : Ratio OOS/IS by quarter (bars + horizontal line at 0.35)

    Colors: blue=normal, red=stress.
    """
    x = np.arange(len(df))
    labels = [_quarter_label(r) for r in df["oos_start"]]
    colors = ["#d62728" if r == "stress" else "#1f77b4" for r in df["regime"]]

    fig, axes = plt.subplots(3, 1, figsize=(max(12, len(df) * 0.7), 10), sharex=True)
    fig.suptitle("TSMOM Walk-Forward Results (SPY+TLT+GLD+DBC+UUP)", fontsize=13)

    # --- Top: Sharpe OOS ---
    ax0 = axes[0]
    sharpes = df["sharpe_oos"].values.astype(float)
    ci_lo = df["ci_lo_95"].values.astype(float)
    ci_hi = df["ci_hi_95"].values.astype(float)

    yerr_lo = np.where(
        np.isnan(ci_lo), 0.0, np.maximum(sharpes - ci_lo, 0.0)
    )
    yerr_hi = np.where(
        np.isnan(ci_hi), 0.0, np.maximum(ci_hi - sharpes, 0.0)
    )

    ax0.bar(x, sharpes, color=colors, alpha=0.8, label="Sharpe OOS")
    ax0.errorbar(
        x,
        sharpes,
        yerr=[yerr_lo, yerr_hi],
        fmt="none",
        color="black",
        capsize=3,
        linewidth=1,
    )
    ax0.axhline(0.8, color="green", linestyle="--", linewidth=1, label="Pass (0.8)")
    ax0.axhline(0.3, color="orange", linestyle="--", linewidth=1, label="Stop (0.3)")
    ax0.axhline(0.0, color="gray", linestyle="-", linewidth=0.5)
    ax0.set_ylabel("Sharpe OOS")
    ax0.legend(fontsize=8, loc="upper right")

    # --- Middle: Max drawdown ---
    ax1 = axes[1]
    dds = df["max_dd_oos"].values.astype(float)
    ax1.bar(x, dds, color=colors, alpha=0.8)
    ax1.axhline(
        0.15, color="orange", linestyle="--", linewidth=1, label="Normal limit (0.15)"
    )
    ax1.axhline(
        0.20, color="red", linestyle="--", linewidth=1, label="Stress limit (0.20)"
    )
    ax1.set_ylabel("Max Drawdown OOS")
    ax1.legend(fontsize=8, loc="upper right")

    # --- Bottom: Ratio OOS/IS ---
    ax2 = axes[2]
    ratios = df["ratio_oos_is"].values.astype(float)
    ax2.bar(x, ratios, color=colors, alpha=0.8)
    ax2.axhline(
        0.35, color="orange", linestyle="--", linewidth=1, label="Min ratio (0.35)"
    )
    ax2.axhline(0.0, color="gray", linestyle="-", linewidth=0.5)
    ax2.set_ylabel("Ratio OOS/IS")
    ax2.legend(fontsize=8, loc="upper right")

    # X-axis labels on the bottom subplot
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax2.set_xlabel("Quarter")

    # Legend for regime colors
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#1f77b4", alpha=0.8, label="Normal"),
        Patch(facecolor="#d62728", alpha=0.8, label="Stress"),
    ]
    fig.legend(
        handles=legend_elements,
        loc="lower center",
        ncol=2,
        fontsize=9,
        bbox_to_anchor=(0.5, 0.0),
    )

    plt.tight_layout(rect=[0, 0.03, 1, 1])
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Chart saved: %s", output_path)


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime

    today_str = datetime.now().strftime("%Y%m%d")
    results_dir = Path(__file__).parent.parent / "results" / "backtest"
    results_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Verify data availability ---
    logger.info("Checking data availability for SPY up to %s ...", _OOS_END)
    check_start = _OOS_END - timedelta(days=10)
    try:
        check_data = YFinanceAdapter().load_ohlcv_daily(["SPY"], check_start, _OOS_END)
        last_available = check_data.index.get_level_values("date").max()
        if isinstance(last_available, pd.Timestamp):
            last_available = last_available.date()
        if last_available < _OOS_END - timedelta(days=7):
            logger.warning(
                "Last available data (%s) is more than 7 days before _OOS_END (%s). "
                "Using available data.",
                last_available,
                _OOS_END,
            )
            effective_oos_end = last_available
        else:
            effective_oos_end = _OOS_END
    except Exception as exc:
        logger.warning("Data check failed: %s. Using _OOS_END as-is.", exc)
        effective_oos_end = _OOS_END

    logger.info("Effective OOS end: %s", effective_oos_end)

    # --- Step 2: Run ONE full backtest for the entire period ---
    logger.info(
        "Running single full backtest %s → %s (tickers: %s) ...",
        _IS_START,
        effective_oos_end,
        _TICKERS,
    )
    equity_full, _ = run_backtest_range(
        _TICKERS, _IS_START, effective_oos_end,
        lookback_months=_LOOKBACK_MONTHS,
        target_vol=_TARGET_VOL,
    )
    logger.info("Full equity curve: %d observations.", len(equity_full))

    # --- Step 3: Generate walk-forward windows ---
    windows = generate_wf_windows(_OOS_START, effective_oos_end)
    logger.info("Generated %d walk-forward windows.", len(windows))

    rows: list[dict] = []

    for i, win in enumerate(windows):
        oos_start = win["oos_start"]
        oos_end = win["oos_end"]
        is_end = win["is_end"]

        # IS equity: full period up to is_end, drop warmup, drop NaN
        equity_is_raw = equity_full.loc[:is_end]
        warmup_cutoff = _LOOKBACK_MONTHS * 21  # 252
        equity_is = equity_is_raw.iloc[warmup_cutoff:].dropna()

        if len(equity_is) > 1:
            std_is = float(equity_is.std())
            sharpe_is = (
                float(equity_is.mean() / std_is * math.sqrt(252))
                if std_is > 1e-12
                else float("nan")
            )
        else:
            sharpe_is = float("nan")

        # OOS equity slice
        equity_oos = equity_full.loc[oos_start:oos_end].dropna()

        if len(equity_oos) > 1:
            report_oos = compute_performance(equity_oos, freq=Frequency.DAILY)
            sharpe_oos = report_oos.sharpe
            max_dd_oos = abs(report_oos.max_drawdown)
        else:
            sharpe_oos = float("nan")
            max_dd_oos = float("nan")

        # Regime classification
        regime = classify_quarter_regime(oos_start, oos_end, _FOMC_RATES)

        # Bootstrap CI
        if len(equity_oos) > 1:
            oos_returns = equity_oos.pct_change().dropna()
        else:
            oos_returns = pd.Series([], dtype=float)

        ci_lo, ci_hi = stationary_block_bootstrap_sharpe_ci(
            oos_returns, n_bootstrap=_BOOTSTRAP_N, block_size=_BOOTSTRAP_BLOCK
        )

        # Gate evaluation
        gate_result = evaluate_gates(sharpe_oos, max_dd_oos, sharpe_is, regime)

        label = _quarter_label(oos_start)
        logger.info(
            "Window %d/%d %s | regime=%s | Sharpe_IS=%.3f | Sharpe_OOS=%.3f | "
            "MaxDD_OOS=%.3f | gate_pass=%s | stop=%s",
            i + 1,
            len(windows),
            label,
            regime,
            sharpe_is if not math.isnan(sharpe_is) else -999,
            sharpe_oos if not math.isnan(sharpe_oos) else -999,
            max_dd_oos if not math.isnan(max_dd_oos) else -999,
            gate_result["gate_pass"],
            gate_result["stop_triggered"],
        )

        rows.append(
            {
                "oos_start": oos_start,
                "oos_end": oos_end,
                "regime": regime,
                "sharpe_is": sharpe_is,
                "sharpe_oos": sharpe_oos,
                "max_dd_oos": max_dd_oos,
                "ratio_oos_is": gate_result["ratio_oos_is"],
                "ci_lo_95": ci_lo,
                "ci_hi_95": ci_hi,
                "gate_pass": gate_result["gate_pass"],
                "stop_triggered": gate_result["stop_triggered"],
                "detail": gate_result["detail"],
            }
        )

    # --- Step 4: Build DataFrame ---
    df_results = pd.DataFrame(rows)

    # --- Step 5: Persist CSV ---
    csv_path = results_dir / f"tsmom_wf_{today_str}.csv"
    df_results.to_csv(csv_path, index=False)
    logger.info("CSV saved: %s", csv_path)

    # --- Step 5.5: Diagnostic 2022Q2 ---
    logger.info("Computing 2022Q2 diagnostic ...")
    try:
        diag_raw = YFinanceAdapter().load_ohlcv_daily(
            _TICKERS, date(2021, 1, 1), date(2022, 6, 30)
        )
        diag_close = diag_raw["close"].unstack("ticker")
        diag_close.index = pd.Index(
            [d.date() if isinstance(d, pd.Timestamp) else d for d in diag_close.index]
        )
        diagnostic_2022q2 = compute_diagnostic_2022q2(diag_close)
    except Exception as exc:
        logger.warning("2022Q2 diagnostic failed: %s", exc)
        diagnostic_2022q2 = {
            "tickers_with_positive_signal": [],
            "note": f"Post-hoc check failed: {exc}",
        }
    logger.info(
        "2022Q2 diagnostic: %s", diagnostic_2022q2["tickers_with_positive_signal"]
    )

    # --- Step 6: Summary JSON ---
    normal_mask = df_results["regime"] == "normal"
    stress_mask = df_results["regime"] == "stress"

    total_windows = len(df_results)
    normal_windows = int(normal_mask.sum())
    stress_windows = int(stress_mask.sum())
    normal_gates_passed = int(df_results.loc[normal_mask, "gate_pass"].sum())
    any_stop = bool(df_results["stop_triggered"].any())

    def _safe_mean(s: pd.Series) -> float:
        clean = s.dropna()
        return float(clean.mean()) if len(clean) > 0 else float("nan")

    sharpe_normal_mean = _safe_mean(df_results.loc[normal_mask, "sharpe_oos"])
    sharpe_stress_mean = _safe_mean(df_results.loc[stress_mask, "sharpe_oos"])

    all_normal_pass = bool(
        normal_windows > 0 and normal_gates_passed == normal_windows
    )
    any_normal_dd_exceed = bool(
        (df_results.loc[normal_mask, "max_dd_oos"] > 0.15).any()
        if normal_windows > 0
        else False
    )
    paper_trading_authorized = bool(
        all_normal_pass and not any_stop and not any_normal_dd_exceed
    )

    def _fmt(v: object) -> object:
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    summary = {
        "total_windows": total_windows,
        "normal_windows": normal_windows,
        "stress_windows": stress_windows,
        "normal_gates_passed": normal_gates_passed,
        "stop_triggered": any_stop,
        "sharpe_oos_normal_mean": _fmt(sharpe_normal_mean),
        "sharpe_oos_stress_mean": _fmt(sharpe_stress_mean),
        "paper_trading_authorized": paper_trading_authorized,
        "statistical_warning": _STATISTICAL_WARNING,
        "diagnostic_2022q2": diagnostic_2022q2,
    }

    json_path = results_dir / f"tsmom_wf_{today_str}_summary.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("JSON saved: %s", json_path)

    # --- Step 7: Plot ---
    png_path = results_dir / f"tsmom_wf_{today_str}.png"
    _plot_wf_results(df_results, png_path)

    # --- Step 8: ASCII table ---
    print("\n" + "=" * 110)
    print(f"{'Quarter':<10} {'Regime':<8} {'Sharpe_IS':>10} {'Sharpe_OOS':>11} "
          f"{'MaxDD_OOS':>10} {'CI_Lo':>7} {'CI_Hi':>7} "
          f"{'Ratio':>7} {'Pass':>5} {'Stop':>5}")
    print("-" * 110)
    for _, row in df_results.iterrows():
        def _f(v: object, fmt: str = ".3f") -> str:
            if isinstance(v, float) and math.isnan(v):
                return "  nan"
            return format(v, fmt)  # type: ignore[call-overload]

        print(
            f"{_quarter_label(row['oos_start']):<10} "
            f"{row['regime']:<8} "
            f"{_f(row['sharpe_is']):>10} "
            f"{_f(row['sharpe_oos']):>11} "
            f"{_f(row['max_dd_oos']):>10} "
            f"{_f(row['ci_lo_95']):>7} "
            f"{_f(row['ci_hi_95']):>7} "
            f"{_f(row['ratio_oos_is']):>7} "
            f"{'YES' if row['gate_pass'] else 'NO':>5} "
            f"{'YES' if row['stop_triggered'] else 'NO':>5}"
        )
    print("=" * 110)
    print("\nSummary:")
    print(f"  Total windows  : {total_windows}")
    print(f"  Normal / Stress: {normal_windows} / {stress_windows}")
    print(f"  Normal gates passed: {normal_gates_passed}/{normal_windows}")
    print(f"  Stop triggered : {any_stop}")
    if not math.isnan(sharpe_normal_mean):
        print(f"  Sharpe OOS mean (normal): {sharpe_normal_mean:.3f}")
    else:
        print("  Sharpe OOS mean (normal): nan")
    if not math.isnan(sharpe_stress_mean):
        print(f"  Sharpe OOS mean (stress): {sharpe_stress_mean:.3f}")
    else:
        print("  Sharpe OOS mean (stress): nan")
    print(f"\n  PAPER TRADING AUTHORIZED: {paper_trading_authorized}")
    print(f"\n  WARNING: {_STATISTICAL_WARNING}")
    print()
