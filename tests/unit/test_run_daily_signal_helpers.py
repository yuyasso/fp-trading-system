"""
tests/unit/test_run_daily_signal_helpers.py
-------------------------------------------
Unit tests for run_daily_signal helpers.
Zero network calls — all fixtures use synthetic DataFrames.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from run_daily_signal import (
    append_to_log,
    compute_current_signals,
    compute_current_weights,
    is_date_already_logged,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_N = 420  # trading days — comfortably > 12 * 21 = 252


def _make_close(
    n: int = _N,
    tickers: list[str] | None = None,
    start_price: float = 100.0,
    end_price: float = 110.0,
) -> pd.DataFrame:
    """Synthetic close prices: linear trend, DatetimeIndex (business days)."""
    if tickers is None:
        tickers = ["A"]
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    data = {t: np.linspace(start_price, end_price, n) for t in tickers}
    return pd.DataFrame(data, index=dates)


def _as_of(close: pd.DataFrame) -> date:
    return close.index[-1].date()


# ---------------------------------------------------------------------------
# compute_current_signals
# ---------------------------------------------------------------------------


def test_signal_positive_lookback() -> None:
    """Ticker with 12m return > 0 → signal = 1."""
    close = _make_close(start_price=100.0, end_price=115.0)
    signals = compute_current_signals(close, _as_of(close), lookback_months=12)
    assert signals["A"] == 1


def test_signal_negative_lookback() -> None:
    """Ticker with 12m return < 0 → signal = 0 (long-only, no shorts)."""
    close = _make_close(start_price=115.0, end_price=100.0)
    signals = compute_current_signals(close, _as_of(close), lookback_months=12)
    assert signals["A"] == 0


def test_signal_insufficient_data() -> None:
    """Ticker with fewer rows than lookback_days → signal = 0."""
    close = _make_close(n=200, start_price=100.0, end_price=115.0)  # 200 < 252
    signals = compute_current_signals(close, _as_of(close), lookback_months=12)
    assert signals["A"] == 0


def test_signal_nan_return() -> None:
    """Most prices NaN → fewer valid rows than lookback → signal=0, no exception."""
    n = _N
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    prices = np.linspace(100.0, 115.0, n)
    prices[: n - 100] = np.nan  # only 100 valid rows remain (< 252)
    close = pd.DataFrame({"A": prices}, index=dates)
    signals = compute_current_signals(close, _as_of(close), lookback_months=12)
    assert signals["A"] == 0


# ---------------------------------------------------------------------------
# compute_current_weights
# ---------------------------------------------------------------------------


def _unit_signals(tickers: list[str], active: list[str]) -> dict[str, int]:
    return {t: (1 if t in active else 0) for t in tickers}


def test_weights_sum_leq_one() -> None:
    """Five tickers all active → normalised weights sum ≤ 1.0."""
    tickers = ["A", "B", "C", "D", "E"]
    close = _make_close(tickers=tickers, start_price=100.0, end_price=110.0)
    signals = _unit_signals(tickers, tickers)  # all active
    weights = compute_current_weights(close, _as_of(close), signals, 0.94, 0.10)
    assert sum(weights.values()) <= 1.0 + 1e-9


def test_weights_zero_when_signal_zero() -> None:
    """Ticker with signal=0 must have weight exactly 0.0."""
    tickers = ["A", "B"]
    close = _make_close(tickers=tickers, start_price=100.0, end_price=110.0)
    signals = {"A": 1, "B": 0}
    weights = compute_current_weights(close, _as_of(close), signals, 0.94, 0.10)
    assert weights["B"] == 0.0


def test_weights_all_signals_zero() -> None:
    """All signals = 0 → all weights = 0.0, no ZeroDivisionError."""
    tickers = ["A", "B", "C"]
    close = _make_close(tickers=tickers)
    signals = {t: 0 for t in tickers}
    weights = compute_current_weights(close, _as_of(close), signals, 0.94, 0.10)
    assert all(w == 0.0 for w in weights.values())


# ---------------------------------------------------------------------------
# is_date_already_logged
# ---------------------------------------------------------------------------


def test_is_date_logged_missing_file(tmp_path: Path) -> None:
    """Non-existent file → False."""
    result = is_date_already_logged(tmp_path / "nonexistent.csv", date(2024, 1, 15))
    assert result is False


def test_is_date_logged_present(tmp_path: Path) -> None:
    """Date that exists in the CSV → True."""
    log = tmp_path / "log.csv"
    log.write_text("date,ticker,signal,weight\n2024-01-15,SPY,1,0.300000\n")
    assert is_date_already_logged(log, date(2024, 1, 15)) is True


def test_is_date_logged_absent(tmp_path: Path) -> None:
    """Date not in CSV → False."""
    log = tmp_path / "log.csv"
    log.write_text("date,ticker,signal,weight\n2024-01-15,SPY,1,0.300000\n")
    assert is_date_already_logged(log, date(2024, 1, 16)) is False


# ---------------------------------------------------------------------------
# append_to_log
# ---------------------------------------------------------------------------

_SIGNALS_5 = {"SPY": 1, "TLT": 0, "GLD": 1, "DBC": 0, "UUP": 1}
_WEIGHTS_5 = {"SPY": 0.30, "TLT": 0.0, "GLD": 0.25, "DBC": 0.0, "UUP": 0.20}
_DATE_A = date(2024, 3, 1)
_DATE_B = date(2024, 3, 4)


def test_append_creates_file(tmp_path: Path) -> None:
    """Append to non-existent file creates CSV with correct header."""
    log = tmp_path / "signal_log.csv"
    append_to_log(log, _DATE_A, _SIGNALS_5, _WEIGHTS_5)
    assert log.exists()
    lines = log.read_text().splitlines()
    assert lines[0] == "date,ticker,signal,weight"


def test_append_idempotent(tmp_path: Path) -> None:
    """Calling append twice for the same date writes only one set of rows."""
    log = tmp_path / "signal_log.csv"
    append_to_log(log, _DATE_A, _SIGNALS_5, _WEIGHTS_5)
    append_to_log(log, _DATE_A, _SIGNALS_5, _WEIGHTS_5)
    date_str = str(_DATE_A)
    data_rows = [ln for ln in log.read_text().splitlines() if ln.startswith(date_str)]
    assert len(data_rows) == len(_SIGNALS_5)


def test_append_row_count(tmp_path: Path) -> None:
    """5 tickers → exactly 5 data rows per date."""
    log = tmp_path / "signal_log.csv"
    append_to_log(log, _DATE_A, _SIGNALS_5, _WEIGHTS_5)
    data_rows = [ln for ln in log.read_text().splitlines() if not ln.startswith("date")]
    assert len(data_rows) == 5


def test_append_sorted_tickers(tmp_path: Path) -> None:
    """Rows are written in alphabetical ticker order."""
    log = tmp_path / "signal_log.csv"
    append_to_log(log, _DATE_A, _SIGNALS_5, _WEIGHTS_5)
    import csv as _csv

    with open(log, newline="") as f:
        rows = list(_csv.DictReader(f))
    tickers_written = [r["ticker"] for r in rows]
    assert tickers_written == sorted(_SIGNALS_5.keys())
