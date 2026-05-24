"""
scripts/run_is.py
-----------------
IS backtest execution script: TSMOM long-only strategy.

Computes sub-period Sharpe analysis, per-asset attribution, saves CSV/JSON/PNG.
The if __name__ == "__main__" block requires network access; helpers are pure/mockable.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.backtest.runner import (
    _compute_ewma_vol_weight,
    _compute_monthly_signal,
    _pivot_ohlcv,
    run_backtest,
)
from trading.domain.metrics.equity_metrics import PerformanceReport, compute_performance

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_LOOKBACK_MONTHS = 12
_TARGET_VOL = 0.10
_IS_START = date(2005, 1, 1)
_IS_END = date(2021, 12, 31)
_TICKERS = ["SPY", "TLT", "GLD", "DBC", "UUP"]

_STATISTICAL_WARNING = (
    "IS Sharpe has insufficient statistical power for strategy adoption decisions. "
    "~17 years IS, monthly rebalancing on 5 assets = O(200) rebalances. "
    "Not a decision criterion."
)

_UNIVERSE_NOTE = (
    "Sub-period 2005-08 Sharpe for UUP is partial (~1yr data). "
    "DBC available from ~2006."
)

_SUB_PERIODS = [
    ("2005-01-01", "2008-12-31"),
    ("2009-01-01", "2013-12-31"),
    ("2014-01-01", "2018-12-31"),
    ("2019-01-01", "2021-12-31"),
]


# ---------------------------------------------------------------------------
# Helper: sub-period Sharpe analysis
# ---------------------------------------------------------------------------


def compute_subperiod_sharpes(
    equity: pd.Series,
    sub_periods: list[tuple[str, str]],
) -> dict[str, float]:
    """
    Compute annualized Sharpe for each sub-period slice of the equity curve.

    Parameters
    ----------
    equity : pd.Series
        Daily return series with datetime.date index.
    sub_periods : list[tuple[str, str]]
        List of (start_iso, end_iso) pairs, e.g. ("2005-01-01", "2008-12-31").

    Returns
    -------
    dict[str, float]
        Keys formatted as "{start_year}-{end_year_last2}", e.g. "2005-08".
        Values are annualized Sharpe ratios; NaN for empty or zero-std slices.
    """
    result: dict[str, float] = {}
    for start_iso, end_iso in sub_periods:
        key = f"{start_iso[:4]}-{end_iso[2:4]}"
        start_d = date.fromisoformat(start_iso)
        end_d = date.fromisoformat(end_iso)
        mask = (equity.index >= start_d) & (equity.index <= end_d)
        slice_ = equity.loc[mask]
        if len(slice_) == 0:
            result[key] = float("nan")
            continue
        std = float(slice_.std())
        if std < 1e-12 or math.isnan(std):
            result[key] = float("nan")
            continue
        result[key] = float(slice_.mean() / std * np.sqrt(252))
    return result


# ---------------------------------------------------------------------------
# Helper: per-asset attribution
# ---------------------------------------------------------------------------


def compute_asset_attribution(
    tickers: list[str],
    start: date,
    end: date,
    lookback_months: int,
    target_vol: float,
) -> tuple[pd.DataFrame, int]:
    """
    Compute per-asset daily return contribution for the given IS period.

    Replicates the runner's signal + sizing logic without averaging across tickers,
    returning a per-column view of contributions.

    Returns
    -------
    attribution : pd.DataFrame
        Columns = tickers, index = date, values = daily contribution per asset.
        NaN propagates from raw prices without fillna/ffill/bfill.
    n_rebalances : int
        Number of monthly rebalance events (non-all-NaN months).
    """
    data = YFinanceAdapter().load_ohlcv_daily(tickers, start, end)
    close = _pivot_ohlcv(data, "close")
    open_ = _pivot_ohlcv(data, "open")

    lookback_days = lookback_months * 21
    position = _compute_monthly_signal(close, lookback_days)
    vol_weight = _compute_ewma_vol_weight(close, target_vol)

    entry_price = open_.shift(-1)
    daily_asset_returns = entry_price.shift(-1) / entry_price - 1.0

    final_position = position * vol_weight
    attribution = final_position * daily_asset_returns

    attribution.index = attribution.index.date
    n_rebalances = len(position.resample("MS").last().dropna(how="all"))

    return attribution, n_rebalances


# ---------------------------------------------------------------------------
# Helper: build metrics dict for JSON serialisation
# ---------------------------------------------------------------------------


def _make_metrics(
    report: PerformanceReport,
    n_rebalances: int,
    warmup_days: int,
    subperiod_sharpes: dict[str, float],
    asset_attribution: dict[str, float],
) -> dict:
    def _fmt(v: float) -> float | None:
        return None if math.isnan(v) else v

    return {
        "sharpe_is": _fmt(report.sharpe),
        "max_drawdown": _fmt(report.max_drawdown),
        "calmar_ratio": _fmt(report.calmar),
        "n_rebalances_is": n_rebalances,
        "warmup_days_excluded": warmup_days,
        "statistical_warning": _STATISTICAL_WARNING,
        "universe_note": _UNIVERSE_NOTE,
        "subperiod_sharpes": {
            k: (_fmt(v) if isinstance(v, float) else v)
            for k, v in subperiod_sharpes.items()
        },
        "asset_attribution": asset_attribution,
    }


# ---------------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------------


def _plot_equity_drawdown(equity_trimmed: pd.Series, out_path: Path) -> None:
    drawdown = (equity_trimmed / equity_trimmed.cummax() - 1) * 100
    idx = pd.to_datetime(equity_trimmed.index.tolist())

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True)
    ax1.plot(idx, equity_trimmed.values)
    ax1.set_title("TSMOM IS Equity Curve")
    ax1.set_ylabel("Daily Return")
    ax2.fill_between(idx, drawdown.values, 0, color="red", alpha=0.4)
    ax2.set_title("Drawdown (%)")
    ax2.set_ylabel("Drawdown (%)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main execution block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    equity_curve, report = run_backtest(
        _TICKERS, _IS_START, _IS_END, _LOOKBACK_MONTHS, _TARGET_VOL
    )

    # Exclude EWMA warmup period then drop trailing NaN from open.shift(-1)
    equity_trimmed = equity_curve.iloc[_LOOKBACK_MONTHS * 21 :].dropna()
    report_trimmed = compute_performance(equity_trimmed)

    subperiod_sharpes = compute_subperiod_sharpes(equity_trimmed, _SUB_PERIODS)

    attribution_df, n_rebalances = compute_asset_attribution(
        _TICKERS, _IS_START, _IS_END, _LOOKBACK_MONTHS, _TARGET_VOL
    )

    out_dir = Path("results/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y%m%d")

    # CSV: equity curve
    equity_trimmed.to_csv(out_dir / f"tsmom_is_{today}.csv", header=True)

    # JSON: metrics
    asset_attr_means = {
        col: float(attribution_df[col].mean()) for col in attribution_df.columns
    }
    metrics = _make_metrics(
        report=report_trimmed,
        n_rebalances=n_rebalances,
        warmup_days=_LOOKBACK_MONTHS * 21,
        subperiod_sharpes=subperiod_sharpes,
        asset_attribution=asset_attr_means,
    )
    with open(out_dir / f"tsmom_is_{today}_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # PNG: equity + drawdown
    _plot_equity_drawdown(equity_trimmed, out_dir / f"tsmom_is_{today}.png")

    # Stdout summary
    sharpe_is = report_trimmed.sharpe
    max_dd = report_trimmed.max_drawdown
    calmar = report_trimmed.calmar
    print(f"Sharpe IS: {sharpe_is:.3f}")
    print(f"Max Drawdown: {max_dd:.1%}")
    print(f"Calmar: {calmar:.3f}")
    print(f"Sub-period Sharpes: {subperiod_sharpes}")
