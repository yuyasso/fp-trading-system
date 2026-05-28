"""
scripts/run_daily_signal.py
---------------------------
Daily forward simulation (phase 0): download D-1 close prices, compute
TSMOM v2 signal and vol-target weights, append to persistent CSV log.

No capital. No broker. Signal logging only.

Usage:
    python scripts/run_daily_signal.py
"""
from __future__ import annotations

import csv
import logging
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — NOT free parameters
# ---------------------------------------------------------------------------

_TICKERS: list[str] = ["SPY", "TLT", "GLD", "DBC", "UUP"]
_LOOKBACK_MONTHS: int = 12
_TARGET_VOL: float = 0.10
_EWMA_LAMBDA: float = 0.94
_LOG_PATH: Path = Path("results/forward_sim/signal_log.csv")
_DOWNLOAD_DAYS: int = 400  # ~13 months of calendar days to guarantee lookback


# ---------------------------------------------------------------------------
# Testable helpers
# ---------------------------------------------------------------------------


def compute_current_signals(
    close: pd.DataFrame,
    as_of: date,
    lookback_months: int,
) -> dict[str, int]:
    """
    Compute long-only TSMOM signals for every ticker in `close` as of `as_of`.

    Parameters
    ----------
    close : pd.DataFrame
        Adjusted close prices. DatetimeIndex, columns = tickers.
    as_of : date
        Reference date — slice ends here inclusive.
    lookback_months : int
        Momentum lookback in months (converted to trading days as months × 21).

    Returns
    -------
    dict[str, int]
        {ticker: 1} if 12m return > 0, {ticker: 0} otherwise.
        Returns 0 for negative return, NaN momentum, or insufficient data.
    """
    as_of_ts = pd.Timestamp(as_of)
    close_slice = close.loc[:as_of_ts]
    lookback_days = lookback_months * 21

    result: dict[str, int] = {}
    for ticker in close.columns:
        col = close_slice[ticker].dropna()
        if len(col) <= lookback_days:
            result[ticker] = 0
            continue
        momentum = col / col.shift(lookback_days) - 1.0
        monthly = momentum.resample("MS").last()
        valid = monthly.dropna()
        if valid.empty or pd.isna(valid.iloc[-1]) or valid.iloc[-1] <= 0:
            result[ticker] = 0
        else:
            result[ticker] = 1
    return result


def compute_current_weights(
    close: pd.DataFrame,
    as_of: date,
    signals: dict[str, int],
    ewma_lambda: float,
    target_vol: float,
) -> dict[str, float]:
    """
    Compute vol-target weights for each ticker as of `as_of`.

    Replicates the EWMA vol-scaling from runner._compute_ewma_vol_weight.
    Applies signal as a mask (weight=0 if signal=0).
    Normalises so total weight ≤ 1.0 (long-only portfolio constraint).

    Parameters
    ----------
    close : pd.DataFrame
        Adjusted close prices. DatetimeIndex, columns = tickers.
    as_of : date
        Reference date — EWMA computed on data up to and including this date.
    signals : dict[str, int]
        Output of compute_current_signals; 0 forces weight to 0.0.
    ewma_lambda : float
        EWMA decay factor (RiskMetrics standard: 0.94).
    target_vol : float
        Annualised vol target (e.g. 0.10 for 10 %).

    Returns
    -------
    dict[str, float]
        {ticker: weight} in [0.0, 1.0].  Sum of values ≤ 1.0.
        All weights are 0.0 when all signals are 0 (no division-by-zero risk).
    """
    as_of_ts = pd.Timestamp(as_of)
    close_slice = close.loc[:as_of_ts]

    daily_returns = close_slice.pct_change()
    ewma_var = daily_returns.pow(2).ewm(alpha=1.0 - ewma_lambda, adjust=False).mean()
    ewma_vol_daily = np.sqrt(ewma_var)
    vol_weight = (target_vol / (math.sqrt(252) * ewma_vol_daily)).clip(upper=1.0)

    last_vol_weights: pd.Series = vol_weight.iloc[-1]

    masked: dict[str, float] = {}
    for ticker in close.columns:
        if signals.get(ticker, 0) == 0:
            masked[ticker] = 0.0
        else:
            raw_w = last_vol_weights.get(ticker, 0.0)
            w = float(raw_w)
            masked[ticker] = 0.0 if math.isnan(w) else w

    total = sum(masked.values())
    if total > 1.0:
        masked = {t: v / total for t, v in masked.items()}

    return {t: float(v) for t, v in masked.items()}


def is_date_already_logged(log_path: Path, check_date: date) -> bool:
    """
    Return True if `check_date` is already present in the log CSV.

    Parameters
    ----------
    log_path : Path
        Path to the signal log CSV.  If the file does not exist, returns False.
    check_date : date
        The date to search for in the ``date`` column.
    """
    if not log_path.exists():
        return False
    with open(log_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == str(check_date):
                return True
    return False


def append_to_log(
    log_path: Path,
    as_of: date,
    signals: dict[str, int],
    weights: dict[str, float],
) -> None:
    """
    Append one row per ticker to the signal log CSV for `as_of`.

    Idempotent: does nothing if `as_of` is already present in the log.
    Rows are written in alphabetical ticker order for reproducibility.
    Creates the file with header ``date,ticker,signal,weight`` if it does not exist.

    Parameters
    ----------
    log_path : Path
        Path to the signal log CSV.
    as_of : date
        The date being logged.
    signals : dict[str, int]
        {ticker: 0 or 1} as returned by compute_current_signals.
    weights : dict[str, float]
        {ticker: weight} as returned by compute_current_weights.
    """
    if is_date_already_logged(log_path, as_of):
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not log_path.exists()

    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "ticker", "signal", "weight"])
        for ticker in sorted(signals.keys()):
            writer.writerow([
                str(as_of),
                ticker,
                signals[ticker],
                f"{weights.get(ticker, 0.0):.6f}",
            ])


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    as_of = date.today()

    if is_date_already_logged(_LOG_PATH, as_of):
        print(f"Ya registrado {as_of}")
        sys.exit(0)

    logger.info(
        "Downloading %d days of close prices for %s ...",
        _DOWNLOAD_DAYS,
        _TICKERS,
    )
    raw = yf.download(
        _TICKERS,
        period=f"{_DOWNLOAD_DAYS}d",
        auto_adjust=True,
        progress=False,
    )
    close: pd.DataFrame = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(name=_TICKERS[0])

    signals = compute_current_signals(close, as_of, _LOOKBACK_MONTHS)
    weights = compute_current_weights(close, as_of, signals, _EWMA_LAMBDA, _TARGET_VOL)

    append_to_log(_LOG_PATH, as_of, signals, weights)

    active = {t: weights[t] for t in sorted(signals) if signals[t] == 1}
    total_weight = sum(weights.values())

    summary = (
        f"Date: {as_of} | "
        f"Long: {sorted(active.keys())} | "
        f"Weights: {', '.join(f'{t}={w:.4f}' for t, w in active.items())} | "
        f"Total: {total_weight:.4f}"
    )
    print(summary)
    logger.info(summary)
