"""
src/trading/backtest/runner.py
------------------------------
TSMOM long-only IS backtest runner.

OOS boundary: 2022-01-01 — DO NOT USE FOR PARAMETER TUNING OR STRATEGY EVALUATION.
Any data from 2022-01-01 onwards is out-of-sample and must not influence this runner.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading.adapters.yfinance_adapter import YFinanceAdapter
from trading.domain.metrics.equity_metrics import (
    Frequency,
    PerformanceReport,
    compute_performance,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — NOT free parameters
# ---------------------------------------------------------------------------

# OOS boundary — hardcoded to prevent any look-ahead into out-of-sample data.
# DO NOT turn this into a function parameter or tune anything using OOS data.
_OOS_START: date = date(2022, 1, 1)

# EWMA decay — RiskMetrics (1994) standard. NOT a free parameter;
# do not expose to callers or tune on IS data.
_EWMA_LAMBDA: float = 0.94


# ---------------------------------------------------------------------------
# Internal helpers (importable for unit tests)
# ---------------------------------------------------------------------------


def _pivot_ohlcv(data: pd.DataFrame, field: str) -> pd.DataFrame:
    """
    Extract one OHLCV field from a (date, ticker) MultiIndex DataFrame and
    return a wide DataFrame indexed by DatetimeIndex with tickers as columns.
    """
    wide = data[field].unstack("ticker")
    wide.index = pd.to_datetime(wide.index)
    return wide


def _compute_monthly_signal(close: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """
    Compute a long-only TSMOM signal, resampled to month-start frequency
    and forward-filled to daily.

    Signal formula: sign(close[t] / close[t - lookback_days] - 1), clipped to [0, 1].
    Rebalanced on the first trading day of each month.

    Parameters
    ----------
    close : pd.DataFrame
        Adjusted close prices with DatetimeIndex, tickers as columns.
    lookback_days : int
        Lookback window in trading days (typically lookback_months × 21).

    Returns
    -------
    pd.DataFrame
        Position indicators aligned to ``close.index``. Values in {0.0, 1.0},
        NaN during the initial lookback warmup period.
    """
    momentum = close / close.shift(lookback_days) - 1.0
    signal = np.sign(momentum).clip(lower=0.0)
    monthly_signal = signal.resample("MS").last()
    return monthly_signal.reindex(close.index, method="ffill")


def _compute_ewma_vol_weight(close: pd.DataFrame, target_vol: float) -> pd.DataFrame:
    """
    Compute per-asset vol-target weights using EWMA variance (λ=0.94).

    weight[asset, t] = target_vol / (sqrt(252) * ewma_daily_vol[asset, t]),
    capped at 1.0 per asset.

    Parameters
    ----------
    close : pd.DataFrame
        Adjusted close prices with DatetimeIndex, tickers as columns.
    target_vol : float
        Annualised volatility target (e.g. 0.10 for 10 %).

    Returns
    -------
    pd.DataFrame
        Per-asset vol-target weights, same shape as ``close``.
    """
    daily_returns = close.pct_change()
    # EWMA variance: α = 1 - λ, pandas ewm convention: y[t] = (1-α)*y[t-1] + α*x[t]
    ewma_var = daily_returns.pow(2).ewm(alpha=1.0 - _EWMA_LAMBDA, adjust=False).mean()
    ewma_vol_daily = np.sqrt(ewma_var)
    return (target_vol / (math.sqrt(252) * ewma_vol_daily)).clip(upper=1.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_backtest(
    tickers: list[str],
    start: date,
    end: date,
    lookback_months: int = 12,
    target_vol: float = 0.10,
) -> tuple[pd.Series, PerformanceReport]:
    """
    Run a TSMOM long-only backtest over the in-sample (IS) period.

    Parameters
    ----------
    tickers : list[str]
        Asset universe to trade (e.g. ["SPY", "TLT", "GLD"]).
    start : date
        First date of the IS period. Must be strictly before 2022-01-01.
    end : date
        Requested last date. Silently clamped to 2021-12-31 if later.
    lookback_months : int
        Momentum lookback in months (converted to days as months × 21).
    target_vol : float
        Annualised per-asset volatility target. Default 10 %.

    Returns
    -------
    equity_curve : pd.Series
        Daily portfolio returns. Index is ``datetime.date``. Name: "tsmom_is".
        NaN values propagate from the adapter without filling.
    report : PerformanceReport
        Risk-adjusted metrics. Computed exclusively by
        ``equity_metrics.compute_performance`` — no metrics logic in this module.

    Raises
    ------
    ValueError
        If ``start`` is on or after _OOS_START (2022-01-01).

    Notes
    -----
    **No data leakage contract** — entry price is next day's open (open[t+1]),
    never close[t]. This is enforced via ``open_.shift(-1)``. Any change to
    the entry price line invalidates all reported metrics.

    **Statistical significance warning** — with only a few years of IS data the
    Sharpe ratio confidence intervals are wide. Do not interpret point estimates
    as reliable forecasts of OOS performance.
    """
    # --- 1. IS/OOS boundary enforcement — always first, before any data access ---
    if start >= _OOS_START:
        raise ValueError(
            f"start date {start} is in OOS period (OOS starts {_OOS_START}). "
            "Restrict backtest development to pre-2022 data."
        )
    end = min(end, _OOS_START - timedelta(days=1))

    # --- 2. Load OHLCV via adapter — no direct yfinance call ---
    data = YFinanceAdapter().load_ohlcv_daily(tickers, start, end)

    close = _pivot_ohlcv(data, "close")
    open_ = _pivot_ohlcv(data, "open")

    # --- 3. Long-only TSMOM signal with monthly rebalancing ---
    lookback_days = lookback_months * 21
    position = _compute_monthly_signal(close, lookback_days)

    # --- 4. Entry at next day's open — NO DATA LEAKAGE ---
    # Signal generated at close[t], executed at open[t+1].
    # Enforced via open_.shift(-1), NOT close. Changing this invalidates all metrics.
    entry_price = open_.shift(-1)

    # Daily asset return: hold from open[t+1] to open[t+2]
    daily_asset_returns = entry_price.shift(-1) / entry_price - 1.0

    # --- 5. EWMA vol-scaling (λ=0.94 — RiskMetrics, not a free parameter) ---
    vol_weight = _compute_ewma_vol_weight(close, target_vol)

    # Final position = long-only signal × vol-scaled weight (per asset)
    final_position = position * vol_weight

    # --- 6. Portfolio equity curve (equal-weight mean across tickers) ---
    portfolio_returns = (final_position * daily_asset_returns).mean(axis=1)
    portfolio_returns.index = portfolio_returns.index.date
    portfolio_returns.name = "tsmom_is"

    # --- 7. Diagnostic logging — regime monitoring, not part of the public contract ---
    recent_corr = close.pct_change().iloc[-60:].corr()
    logger.info("Last-60d asset correlation:\n%s", recent_corr)

    rebalance_count = len(position.resample("MS").last().dropna(how="all"))
    logger.info(
        "IS period rebalances: %d. "
        "WARNING: With approximately %d months of IS data, statistical significance "
        "is low. Sharpe estimates carry wide confidence intervals. "
        "Interpret IS results with caution and do not tune parameters on this data.",
        rebalance_count,
        rebalance_count,
    )

    # --- 8. Performance metrics — delegated entirely to equity_metrics ---
    report = compute_performance(portfolio_returns, freq=Frequency.DAILY)

    return portfolio_returns, report
