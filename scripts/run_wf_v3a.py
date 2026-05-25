"""
scripts/run_wf_v3a.py
---------------------
Walk-forward TSMOM v3a with SPY/TLT 60d rolling correlation overlay.

Overlay logic: when the SPY/TLT 60-day rolling Pearson correlation exceeds
the IS P90 threshold (fixed on 2005–2021 data), target_vol is halved (× 0.5).
The threshold is computed once from IS data and applied unchanged to OOS.

Anti-overfitting gate: the overlay must fire ≥ 2 distinct times in IS (2005–2021).
If not, the script exits with code 1 — the overlay is too selective to be credible.

Usage:
    python scripts/run_wf_v3a.py

Outputs (results/backtest/):
    tsmom_wf_v3a_YYYYMMDD.csv
    tsmom_wf_v3a_YYYYMMDD_summary.json
    tsmom_wf_v3a_YYYYMMDD.png
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from run_wf import (
    _FOMC_RATES,
    _quarter_label,
    classify_quarter_regime,
    evaluate_gates,
    generate_wf_windows,
    stationary_block_bootstrap_sharpe_ci,
)

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.backtest.runner import (
    _compute_ewma_vol_weight,
    _compute_monthly_signal,
    _pivot_ohlcv,
)
from trading.domain.metrics.equity_metrics import (
    Frequency,
    PerformanceReport,
    compute_performance,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — NOT free parameters
# ---------------------------------------------------------------------------

_TICKERS = ["SPY", "TLT", "GLD", "DBC", "UUP"]
_LOOKBACK_MONTHS = 12
_TARGET_VOL = 0.10
_EWMA_LAMBDA = 0.94  # RiskMetrics standard — always 0.94
_CORR_WINDOW = 60  # business days
_CORR_REDUCTION = 0.5  # target_vol × 0.5 when overlay active
_CORR_PERCENTILE = 90.0  # IS P90 threshold
_IS_START = date(2005, 1, 1)
_IS_END = date(2021, 12, 31)
_OOS_END = date(2026, 4, 30)

_BOOTSTRAP_N = 1000
_BOOTSTRAP_BLOCK = 21

_STATISTICAL_WARNING = (
    "~63 obs/trimestre; intervalos bootstrap amplios. "
    "No inferir significancia de ventanas individuales. "
    "Overlay correlacion SPY/TLT experimental — no inferir causalidad."
)


# ---------------------------------------------------------------------------
# Testable helpers
# ---------------------------------------------------------------------------


def compute_rolling_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    window: int,
) -> pd.Series:
    """
    Rolling Pearson correlation between two daily-returns series.

    Parameters
    ----------
    series_a, series_b : pd.Series
        Daily returns (NOT prices). Apply pct_change() before calling.
    window : int
        Rolling window length in trading days.

    Returns
    -------
    pd.Series
        Same length as inputs; first ``window - 1`` positions are NaN.
    """
    return series_a.rolling(window=window).corr(series_b)


def compute_correlation_threshold(
    close_spy: pd.Series,
    close_tlt: pd.Series,
    window: int,
    percentile: float,
) -> float:
    """
    Compute a fixed correlation threshold as a percentile of the IS distribution.

    Parameters
    ----------
    close_spy, close_tlt : pd.Series
        Adjusted close prices. pct_change() applied internally.
    window : int
        Rolling window in trading days.
    percentile : float
        Target percentile (e.g. 90.0 for P90).

    Returns
    -------
    float
        Single scalar threshold.
    """
    corr = compute_rolling_correlation(
        close_spy.pct_change(),
        close_tlt.pct_change(),
        window,
    )
    return float(np.percentile(corr.dropna().values, percentile))


def apply_correlation_overlay(
    vol_weights: pd.DataFrame,
    correlation: pd.Series,
    threshold: float,
    reduction_factor: float,
) -> pd.DataFrame:
    """
    Reduce portfolio weights on days where correlation exceeds the threshold.

    Parameters
    ----------
    vol_weights : pd.DataFrame
        Per-asset weights with DatetimeIndex or date index.
    correlation : pd.Series
        Rolling correlation alignable to vol_weights.index.
    threshold : float
        Activation threshold.
    reduction_factor : float
        Multiplier on active days (e.g. 0.5 halves the weights).

    Returns
    -------
    pd.DataFrame
        Same shape. Active days scaled by reduction_factor; NaN or
        ≤ threshold days are unchanged.
    """
    result = vol_weights.copy()
    aligned_corr = correlation.reindex(vol_weights.index)
    # NaN comparisons yield False — NaN days are not reduced
    mask = (aligned_corr > threshold).fillna(False)
    result.loc[mask] = result.loc[mask] * reduction_factor
    return result


def count_overlay_events(
    correlation: pd.Series,
    threshold: float,
    min_gap_days: int = 20,
) -> list[tuple[date, date]]:
    """
    Detect distinct overlay activation periods.

    Two active periods separated by fewer than ``min_gap_days`` calendar days
    are merged into one event.

    Parameters
    ----------
    correlation : pd.Series
        Rolling correlation with DatetimeIndex or date index.
    threshold : float
        Activation threshold.
    min_gap_days : int
        Minimum calendar-day gap between distinct events (default 20).

    Returns
    -------
    list[tuple[date, date]]
        Each tuple is (event_start, event_end) as datetime.date objects.
    """
    active = correlation[correlation > threshold].dropna()
    if len(active) == 0:
        return []

    active_dates: list[date] = sorted(
        d.date() if isinstance(d, pd.Timestamp) else d
        for d in active.index
    )

    events: list[tuple[date, date]] = []
    cur_start = active_dates[0]
    cur_end = active_dates[0]

    for d in active_dates[1:]:
        gap = (d - cur_end).days
        if gap <= min_gap_days:
            cur_end = d
        else:
            events.append((cur_start, cur_end))
            cur_start = d
            cur_end = d
    events.append((cur_start, cur_end))
    return events


def compute_mean_exposure(
    vol_weights: pd.DataFrame,
    start: date,
    end: date,
) -> float:
    """
    Fraction of days in [start, end] where the portfolio has any exposure.

    A day has exposure when the sum of all weights > 0.

    Parameters
    ----------
    vol_weights : pd.DataFrame
        Per-asset weights with DatetimeIndex or date index.
    start, end : date
        Inclusive date range.

    Returns
    -------
    float
        Value in [0.0, 1.0], or float('nan') if the range contains no data.
    """
    if len(vol_weights) == 0:
        return float("nan")

    first_idx = vol_weights.index[0]
    if isinstance(first_idx, pd.Timestamp):
        subset = vol_weights.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    else:
        subset = vol_weights.loc[start:end]

    if len(subset) == 0:
        return float("nan")

    has_exposure = subset.sum(axis=1) > 0
    return float(has_exposure.mean())


def run_tsmom_v3a_window(
    tickers: list[str],
    is_start: date,
    oos_start: date,
    oos_end: date,
    corr_threshold: float,
    lookback_months: int,
    target_vol: float,
    ewma_lambda: float,
    corr_window: int,
    reduction_factor: float,
) -> tuple[pd.Series, PerformanceReport]:
    """
    Run a single expanding-window TSMOM v3a backtest, returning OOS equity.

    Loads OHLCV from is_start to oos_end, applies SPY/TLT correlation overlay,
    and returns the OOS-period equity slice with its performance report.

    Parameters
    ----------
    ewma_lambda : float
        API parameter; always 0.94 in practice (RiskMetrics constant via runner).
    """
    data = YFinanceAdapter().load_ohlcv_daily(tickers, is_start, oos_end)
    close = _pivot_ohlcv(data, "close")
    open_ = _pivot_ohlcv(data, "open")

    lookback_days = lookback_months * 21
    position = _compute_monthly_signal(close, lookback_days)
    entry_price = open_.shift(-1)
    daily_asset_returns = entry_price.shift(-1) / entry_price - 1.0
    vol_weight = _compute_ewma_vol_weight(close, target_vol)

    corr = compute_rolling_correlation(
        close["SPY"].pct_change(),
        close["TLT"].pct_change(),
        corr_window,
    )
    vol_weight_overlaid = apply_correlation_overlay(
        vol_weight, corr, corr_threshold, reduction_factor
    )

    final_position = position * vol_weight_overlaid
    portfolio_returns = (final_position * daily_asset_returns).mean(axis=1)
    portfolio_returns.index = portfolio_returns.index.date
    portfolio_returns.name = "tsmom_v3a"

    equity_oos = portfolio_returns.loc[oos_start:oos_end]
    report = compute_performance(equity_oos.dropna(), freq=Frequency.DAILY)
    return equity_oos, report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _event_overlaps(
    events: list[tuple[date, date]], check_start: date, check_end: date
) -> bool:
    """Return True if any event overlaps with [check_start, check_end]."""
    for start, end in events:
        if start <= check_end and end >= check_start:
            return True
    return False


def _plot_wf_v3a_results(
    df: pd.DataFrame,
    corr_full: pd.Series,
    corr_threshold: float,
    final_position: pd.DataFrame,
    windows: list[dict],
    output_path: Path,
) -> None:
    """
    3-subplot walk-forward results chart.

    Top    : Sharpe OOS by quarter (bars, green=gate pass, red=gate fail)
    Middle : SPY/TLT 60d rolling correlation over IS + OOS, with threshold line
    Bottom : Mean daily exposure by quarter (bars)
    """
    x = np.arange(len(df))
    labels = [_quarter_label(r) for r in df["oos_start"]]
    colors = ["#2ca02c" if gp else "#d62728" for gp in df["gate_pass"]]

    fig, axes = plt.subplots(3, 1, figsize=(max(14, len(df) * 0.8), 12))
    fig.suptitle(
        "TSMOM v3a Walk-Forward — SPY/TLT 60d Overlay (SPY+TLT+GLD+DBC+UUP)",
        fontsize=12,
    )

    # --- Top: Sharpe OOS ---
    ax0 = axes[0]
    sharpes = df["sharpe_oos"].values.astype(float)
    ax0.bar(x, sharpes, color=colors, alpha=0.85)
    ax0.axhline(0.8, color="green", linestyle="--", linewidth=1, label="Pass (0.8)")
    ax0.axhline(0.3, color="orange", linestyle="--", linewidth=1, label="Stop (0.3)")
    ax0.axhline(0.0, color="gray", linestyle="-", linewidth=0.5)
    ax0.set_ylabel("Sharpe OOS")
    ax0.set_title("Sharpe OOS por trimestre  (verde=gate pass, rojo=gate fail)")
    ax0.legend(fontsize=8, loc="upper right")

    # --- Middle: Rolling correlation ---
    ax1 = axes[1]
    if len(corr_full) > 0:
        corr_idx = pd.to_datetime(corr_full.index) if not isinstance(
            corr_full.index[0], pd.Timestamp
        ) else corr_full.index
        ax1.plot(
            corr_idx,
            corr_full.values,
            color="#1f77b4",
            linewidth=0.7,
            label="SPY/TLT 60d corr",
        )
    ax1.axhline(
        corr_threshold,
        color="red",
        linestyle="--",
        linewidth=1.2,
        label=f"Threshold IS P90 ({corr_threshold:.3f})",
    )
    ax1.axvline(
        pd.Timestamp(_IS_END),
        color="black",
        linestyle=":",
        linewidth=1,
        label="IS/OOS boundary",
    )
    ax1.set_ylabel("Rolling Correlation")
    ax1.set_title("SPY/TLT 60d rolling correlation — IS + OOS")
    ax1.legend(fontsize=8, loc="upper right")

    # --- Bottom: Mean exposure by quarter ---
    ax2 = axes[2]
    exposures = [
        compute_mean_exposure(final_position, w["oos_start"], w["oos_end"])
        for w in windows
    ]
    valid_exp = [0.0 if math.isnan(e) else e for e in exposures]
    ax2.bar(x, valid_exp, color=colors, alpha=0.85)
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("Mean Exposure")
    ax2.set_title("Exposicion media diaria por trimestre")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax2.set_xlabel("Quarter")

    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#2ca02c", alpha=0.85, label="Gate Pass"),
        Patch(facecolor="#d62728", alpha=0.85, label="Gate Fail"),
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
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime

    today_str = datetime.now().strftime("%Y%m%d")
    results_dir = Path(__file__).parent.parent / "results" / "backtest"
    results_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Data availability check ---
    logger.info("Checking data availability for SPY up to %s ...", _OOS_END)
    check_start = _OOS_END - timedelta(days=10)
    try:
        check_data = YFinanceAdapter().load_ohlcv_daily(["SPY"], check_start, _OOS_END)
        last_available = check_data.index.get_level_values("date").max()
        if isinstance(last_available, pd.Timestamp):
            last_available = last_available.date()
        effective_oos_end = (
            last_available
            if last_available >= _OOS_END - timedelta(days=7)
            else last_available
        )
    except Exception as exc:
        logger.warning("Data check failed: %s. Using _OOS_END as-is.", exc)
        effective_oos_end = _OOS_END
    logger.info("Effective OOS end: %s", effective_oos_end)

    # --- Step 2: Load full OHLCV once (IS + OOS) ---
    logger.info(
        "Loading OHLCV %s -> %s for tickers: %s",
        _IS_START,
        effective_oos_end,
        _TICKERS,
    )
    data = YFinanceAdapter().load_ohlcv_daily(_TICKERS, _IS_START, effective_oos_end)
    close = _pivot_ohlcv(data, "close")
    open_ = _pivot_ohlcv(data, "open")
    logger.info("OHLCV loaded: %d rows × %d tickers", len(close), len(close.columns))

    # --- Step 3: Compute rolling correlation (full period) ---
    corr_full = compute_rolling_correlation(
        close["SPY"].pct_change(),
        close["TLT"].pct_change(),
        _CORR_WINDOW,
    )

    # IS slice of correlation for threshold computation
    is_start_ts = pd.Timestamp(_IS_START)
    is_end_ts = pd.Timestamp(_IS_END)
    corr_is = corr_full[
        (corr_full.index >= is_start_ts) & (corr_full.index <= is_end_ts)
    ]
    corr_threshold = float(np.percentile(corr_is.dropna().values, _CORR_PERCENTILE))
    logger.info(
        "IS P%g correlation threshold (SPY/TLT, %dd window): %.4f",
        _CORR_PERCENTILE,
        _CORR_WINDOW,
        corr_threshold,
    )

    # --- Step 4: IS overlay events ---
    is_events = count_overlay_events(corr_is, corr_threshold)
    logger.info("IS overlay events (%d total):", len(is_events))
    for s, e in is_events:
        logger.info("  %s → %s", s, e)

    # Check historic stress events
    taper_tantrum_fired = _event_overlaps(
        is_events, date(2013, 5, 1), date(2013, 6, 30)
    )
    covid_2020_fired = _event_overlaps(is_events, date(2020, 3, 1), date(2020, 3, 31))
    print(
        f"\nTaper Tantrum 2013 (May-Jun 2013): overlay fired = {taper_tantrum_fired}"
    )
    print(f"COVID 2020 (March 2020):           overlay fired = {covid_2020_fired}\n")

    # --- Step 5: Anti-overfitting gate ---
    # IS is 2005-2021, so all IS events are outside 2022 by definition.
    # Gate: at least 2 distinct IS events → overlay is not exclusively a 2022 artefact.
    events_outside_2022 = is_events  # all IS events are pre-2022
    if len(events_outside_2022) < 2:
        print(
            f"ERROR: Anti-overfitting gate FAILED.\n"
            f"  IS P{_CORR_PERCENTILE:.0f} threshold = {corr_threshold:.4f}\n"
            f"  IS overlay events = {len(is_events)} (need ≥ 2)\n"
            f"  Events: {is_events}\n"
            "The overlay fires too rarely in IS to be credible outside 2022.\n"
            "Raise _CORR_PERCENTILE to lower the threshold, or reconsider the overlay."
        )
        sys.exit(1)
    logger.info(
        "Anti-overfitting gate PASSED: %d IS events (≥ 2 required).",
        len(events_outside_2022),
    )

    # --- Step 6: Build full equity curve with overlay ---
    lookback_days = _LOOKBACK_MONTHS * 21
    position = _compute_monthly_signal(close, lookback_days)
    entry_price = open_.shift(-1)
    daily_asset_returns = entry_price.shift(-1) / entry_price - 1.0
    vol_weight = _compute_ewma_vol_weight(close, _TARGET_VOL)

    vol_weight_overlaid = apply_correlation_overlay(
        vol_weight, corr_full, corr_threshold, _CORR_REDUCTION
    )

    final_position = position * vol_weight_overlaid
    portfolio_returns = (final_position * daily_asset_returns).mean(axis=1)
    portfolio_returns.index = portfolio_returns.index.date
    portfolio_returns.name = "tsmom_v3a"
    logger.info("Full equity curve: %d observations.", len(portfolio_returns))

    # --- Step 7: Generate 18 WF windows (2022Q1 → 2026Q2) ---
    oos_global_start = date(2022, 1, 1)
    windows = generate_wf_windows(oos_global_start, effective_oos_end)
    logger.info("Generated %d walk-forward windows.", len(windows))

    rows: list[dict] = []

    for i, win in enumerate(windows):
        oos_start = win["oos_start"]
        oos_end_w = win["oos_end"]
        is_end = win["is_end"]

        # IS equity: drop warmup, drop NaN
        equity_is_raw = portfolio_returns.loc[:is_end]
        warmup_cutoff = _LOOKBACK_MONTHS * 21
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
        equity_oos = portfolio_returns.loc[oos_start:oos_end_w].dropna()

        if len(equity_oos) > 1:
            report_oos = compute_performance(equity_oos, freq=Frequency.DAILY)
            sharpe_oos = report_oos.sharpe
            max_dd_oos = abs(report_oos.max_drawdown)
        else:
            sharpe_oos = float("nan")
            max_dd_oos = float("nan")

        regime = classify_quarter_regime(oos_start, oos_end_w, _FOMC_RATES)

        # Bootstrap CI
        if len(equity_oos) > 1:
            oos_returns = equity_oos.pct_change().dropna()
        else:
            oos_returns = pd.Series([], dtype=float)
        ci_lo, ci_hi = stationary_block_bootstrap_sharpe_ci(
            oos_returns, n_bootstrap=_BOOTSTRAP_N, block_size=_BOOTSTRAP_BLOCK
        )

        gate_result = evaluate_gates(sharpe_oos, max_dd_oos, sharpe_is, regime)
        label = _quarter_label(oos_start)

        logger.info(
            "Window %d/%d %s | regime=%s | Sharpe_IS=%.3f | Sharpe_OOS=%.3f | "
            "MaxDD=%.3f | gate=%s | stop=%s",
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
                "quarter": label,
                "oos_start": oos_start,
                "oos_end": oos_end_w,
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

    # --- Step 8: Build results DataFrame ---
    df_results = pd.DataFrame(rows)

    # --- Step 9: Compute exposure metrics ---
    exposure_2022q2 = compute_mean_exposure(
        final_position, date(2022, 4, 1), date(2022, 6, 30)
    )
    exposure_2022q4 = compute_mean_exposure(
        final_position, date(2022, 10, 1), date(2022, 12, 31)
    )
    logger.info(
        "Exposure 2022Q2=%.3f  2022Q4=%.3f",
        exposure_2022q2 if not math.isnan(exposure_2022q2) else -1,
        exposure_2022q4 if not math.isnan(exposure_2022q4) else -1,
    )

    # --- Step 10: Global gate evaluation ---
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

    all_normal_pass = bool(normal_windows > 0 and normal_gates_passed == normal_windows)
    any_normal_dd_exceed = bool(
        (df_results.loc[normal_mask, "max_dd_oos"] > 0.15).any()
        if normal_windows > 0
        else False
    )
    paper_trading_authorized = bool(
        all_normal_pass and not any_stop and not any_normal_dd_exceed
    )

    # stop_quarter: first quarter where stop_triggered=True (never absent from JSON)
    stop_rows = df_results[df_results["stop_triggered"]]
    stop_quarter_val: str | None = (
        _quarter_label(stop_rows.iloc[0]["oos_start"])
        if len(stop_rows) > 0
        else None
    )

    # --- Step 11: Persist CSV ---
    csv_path = results_dir / f"tsmom_wf_v3a_{today_str}.csv"
    df_results.to_csv(csv_path, index=False)
    logger.info("CSV saved: %s", csv_path)

    # --- Step 12: Persist JSON ---
    def _fmt(v: object) -> object:
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    summary = {
        "correlation_p90_threshold": corr_threshold,
        "corr_is_events": [
            {"start": s.isoformat(), "end": e.isoformat()} for s, e in is_events
        ],
        "taper_tantrum_2013_fired": taper_tantrum_fired,
        "covid_2020_fired": covid_2020_fired,
        "exposure_2022q2": _fmt(exposure_2022q2),
        "exposure_2022q4": _fmt(exposure_2022q4),
        "paper_trading_authorized": paper_trading_authorized,
        "stop_triggered": any_stop,
        "stop_quarter": stop_quarter_val,
        "statistical_warning": _STATISTICAL_WARNING,
        # additional context
        "total_windows": total_windows,
        "normal_windows": normal_windows,
        "stress_windows": stress_windows,
        "normal_gates_passed": normal_gates_passed,
        "sharpe_oos_normal_mean": _fmt(sharpe_normal_mean),
        "sharpe_oos_stress_mean": _fmt(sharpe_stress_mean),
    }

    json_path = results_dir / f"tsmom_wf_v3a_{today_str}_summary.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("JSON saved: %s", json_path)

    # --- Step 13: Plot ---
    png_path = results_dir / f"tsmom_wf_v3a_{today_str}.png"
    _plot_wf_v3a_results(
        df_results, corr_full, corr_threshold, final_position, windows, png_path
    )

    # --- Step 14: ASCII summary ---
    print("\n" + "=" * 115)
    print(
        f"{'Quarter':<10} {'Regime':<8} {'Sharpe_IS':>10} {'Sharpe_OOS':>11} "
        f"{'MaxDD_OOS':>10} {'CI_Lo':>7} {'CI_Hi':>7} "
        f"{'Ratio':>7} {'Pass':>5} {'Stop':>5}"
    )
    print("-" * 115)
    for _, row in df_results.iterrows():

        def _f(v: object, fmt: str = ".3f") -> str:
            if isinstance(v, float) and math.isnan(v):
                return "  nan"
            return format(v, fmt)  # type: ignore[call-overload]

        print(
            f"{row['quarter']:<10} "
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
    print("=" * 115)
    print(f"\nCorrelation threshold (IS P{_CORR_PERCENTILE:.0f}): {corr_threshold:.4f}")
    print(f"IS overlay events: {len(is_events)}")
    print(f"Taper Tantrum 2013 fired: {taper_tantrum_fired}")
    print(f"COVID 2020 fired:         {covid_2020_fired}")
    print(f"\nExposure 2022Q2 (overlay active): {_fmt(exposure_2022q2)}")
    print(f"Exposure 2022Q4 (overlay active): {_fmt(exposure_2022q4)}")
    print(f"\nNormal windows: {normal_windows}  |  Stress windows: {stress_windows}")
    print(f"Normal gates passed: {normal_gates_passed}/{normal_windows}")
    print(f"Stop triggered: {any_stop}  (first quarter: {stop_quarter_val})")
    if not math.isnan(sharpe_normal_mean):
        print(f"Sharpe OOS mean (normal): {sharpe_normal_mean:.3f}")
    if not math.isnan(sharpe_stress_mean):
        print(f"Sharpe OOS mean (stress): {sharpe_stress_mean:.3f}")
    print(f"\nPAPER TRADING AUTHORIZED: {paper_trading_authorized}")
    print(f"\nWARNING: {_STATISTICAL_WARNING}")
    print()
