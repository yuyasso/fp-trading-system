"""
domain/metrics/equity_metrics.py
---------------------------------
Pure-domain performance metrics: no infrastructure dependencies.
Allowed imports: numpy, pandas, enum, dataclasses, stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Frequency enum
# ---------------------------------------------------------------------------

class Frequency(StrEnum):
    """Return frequency, used to select the annualization factor."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ---------------------------------------------------------------------------
# PerformanceReport dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PerformanceReport:
    """
    Immutable container for risk-adjusted performance metrics.

    Fields
    ------
    sharpe : float
        Annualized Sharpe ratio. NaN when std == 0 or series is empty.
    sortino : float
        Annualized Sortino ratio. NaN when downside std == 0 or series is empty.
    calmar : float
        Calmar ratio (annualized_return / abs(max_drawdown)).
        NaN when max_drawdown == 0.
    max_drawdown : float
        Maximum drawdown as a negative fraction, e.g. -0.12.
        NaN when series is empty.
    max_drawdown_duration : int
        Number of series periods from the prior peak to the drawdown recovery
        (or to the end of the series if no recovery). 0 for empty/no-drawdown.
    annualized_return : float
        Mean periodic return multiplied by the annualization factor. NaN when
        series is empty.
    n_periods : int
        Number of observations in the input series.
    frequency : Frequency
        Return frequency used for annualization. Allows callers to convert
        max_drawdown_duration from periods to calendar time.
    """

    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    max_drawdown_duration: int
    annualized_return: float
    n_periods: int
    frequency: Frequency


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ann_factor(freq: Frequency) -> int:
    """Return the annualization factor for the given frequency."""
    if freq == Frequency.DAILY:
        return TRADING_DAYS_PER_YEAR
    if freq == Frequency.WEEKLY:
        return 52
    return 12  # MONTHLY


def _empty_report(freq: Frequency) -> PerformanceReport:
    """Return a report with NaN ratios for degenerate inputs (e.g. empty series)."""
    nan = float("nan")
    return PerformanceReport(
        sharpe=nan,
        sortino=nan,
        calmar=nan,
        max_drawdown=nan,
        max_drawdown_duration=0,
        annualized_return=nan,
        n_periods=0,
        frequency=freq,
    )


def _compute_max_drawdown(equity: np.ndarray) -> tuple[float, int]:
    """
    Compute maximum drawdown and its duration in periods.

    Parameters
    ----------
    equity : np.ndarray
        Cumulative equity curve (already computed from returns).

    Returns
    -------
    max_drawdown : float
        Most negative relative trough-from-peak, e.g. -0.20.
    duration : int
        Periods between the peak prior to the worst trough and the first
        recovery to that peak level (or len(equity) if no recovery occurs).
    """
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(np.min(drawdown))

    if max_dd == 0.0:
        return 0.0, 0

    trough_pos = int(np.argmin(drawdown))

    # Last position at or before the trough where equity was at a running peak
    at_peak = equity[: trough_pos + 1] == running_max[: trough_pos + 1]
    peak_candidates = np.where(at_peak)[0]
    peak_pos = int(peak_candidates[-1]) if len(peak_candidates) > 0 else 0

    # First position on or after the trough where equity recovers to the peak level
    peak_value = running_max[trough_pos]
    post_trough_equity = equity[trough_pos:]
    recovery_candidates = np.where(post_trough_equity >= peak_value)[0]

    if len(recovery_candidates) > 0:
        # When max_dd < 0, equity[trough_pos] < peak_value, so recovered[0]
        # corresponds to a point strictly after the trough.
        recovery_pos = trough_pos + int(recovery_candidates[0])
    else:
        recovery_pos = len(equity)  # series ended before recovery

    return max_dd, recovery_pos - peak_pos


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_performance(
    series: pd.Series,  # type: ignore[type-arg]
    rf: float = 0.0,
    target_return: float = 0.0,
    freq: Frequency = Frequency.DAILY,
) -> PerformanceReport:
    """
    Compute risk-adjusted performance metrics from a series of periodic returns.

    Parameters
    ----------
    series : pd.Series
        Periodic returns (not a cumulative equity curve). Each element is the
        return for one period, e.g. 0.01 for +1 %.
    rf : float
        Annualized risk-free rate. Converted to per-period via rf / ann_factor
        before computing excess returns.
    target_return : float
        Per-period minimum acceptable return used in the Sortino downside
        deviation. Defaults to 0.0 (i.e., any negative return is "downside").
    freq : Frequency
        Return frequency, selects annualization factor:
        DAILY → 252, WEEKLY → 52, MONTHLY → 12.

    Returns
    -------
    PerformanceReport
        Frozen dataclass. All ratio fields return float('nan') on degenerate
        inputs (empty series, zero variance, zero drawdown for Calmar) —
        no exceptions are raised so pipelines remain uninterrupted.

    Notes
    -----
    Annualization convention
        Vol-based ratios (Sharpe, Sortino) are scaled by sqrt(ann_factor).
        Returns are scaled by ann_factor (arithmetic, not geometric).

    rf convention
        rf is annualized; the per-period adjustment is rf / ann_factor,
        matching the standard IID Sharpe derivation.

    Sharpe limitation under autocorrelation
        When returns exhibit positive autocorrelation (common in trend-following
        / momentum strategies), the IID Sharpe ratio overstates risk-adjusted
        performance because sqrt(T) scaling of mean returns is inflated.
        The opposite applies to mean-reversion strategies with negative
        autocorrelation.

    # TODO (Lo 2002): Sharpe ajustado por autocorrelación — activar si los
    # returns muestran autocorrelación significativa (típico en mean-reversion
    # intradiaria o momentum con momentum). Ver autocorrelation-adjusted
    # Sharpe ratio.
    """
    # Normalize freq — raises ValueError for invalid strings
    freq = Frequency(freq)

    n = len(series)
    if n == 0:
        return _empty_report(freq)

    factor = _ann_factor(freq)
    rf_period = rf / factor

    vals: np.ndarray = series.to_numpy(dtype=float)

    # --- Annualized return (arithmetic) ---
    mean_r = float(np.mean(vals))
    annualized_return = mean_r * factor

    # --- Sharpe ---
    std_r = float(series.std())  # ddof=1 (pandas default)
    # Guard against numerical near-zero std: constant returns produce a tiny
    # non-zero residual (~1e-18) due to IEEE 754 floating-point arithmetic.
    # Any std below 1e-12 is physically indistinguishable from zero for returns.
    if std_r < 1e-12 or np.isnan(std_r):
        sharpe = float("nan")
    else:
        sharpe = (mean_r - rf_period) / std_r * np.sqrt(factor)

    # --- Sortino ---
    downside_excess = np.minimum(vals - target_return, 0.0)
    downside_var = float(np.mean(downside_excess**2))
    if downside_var == 0.0:
        sortino = float("nan")
    else:
        downside_std = float(np.sqrt(downside_var))
        sortino = (mean_r - rf_period) / downside_std * np.sqrt(factor)

    # --- Max drawdown & duration ---
    equity: np.ndarray = np.cumprod(1.0 + vals)
    max_drawdown, max_drawdown_duration = _compute_max_drawdown(equity)

    # --- Calmar ---
    if max_drawdown == 0.0:
        calmar = float("nan")
    else:
        calmar = annualized_return / abs(max_drawdown)

    return PerformanceReport(
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_drawdown_duration,
        annualized_return=annualized_return,
        n_periods=n,
        frequency=freq,
    )


def rolling_sharpe(
    series: pd.Series,  # type: ignore[type-arg]
    window: int,
    min_periods: int = 60,
    rf: float = 0.0,
) -> pd.Series:  # type: ignore[type-arg]
    """
    Compute a rolling annualized Sharpe ratio.

    Parameters
    ----------
    series : pd.Series
        Periodic returns.
    window : int
        Rolling window length in periods.
    min_periods : int
        Minimum number of observations required to produce a value; positions
        with fewer observations yield NaN. Defaults to 60.
    rf : float
        Annualized risk-free rate. Per-period adjustment assumes daily
        frequency (rf / TRADING_DAYS_PER_YEAR).

    Returns
    -------
    pd.Series
        Rolling Sharpe ratios, aligned to the input index. NaN wherever
        the number of valid observations is below min_periods.

    Warning
    -------
    Windows shorter than 60 periods have very high statistical variance.
    Confidence intervals for Sharpe ratios widen substantially with small
    samples; treat short-window estimates as indicative only.
    """
    rf_period = rf / TRADING_DAYS_PER_YEAR
    roll = series.rolling(window=window, min_periods=min_periods)
    rolling_mean = roll.mean()
    rolling_std = roll.std()  # ddof=1
    return (rolling_mean - rf_period) / rolling_std * np.sqrt(TRADING_DAYS_PER_YEAR)
