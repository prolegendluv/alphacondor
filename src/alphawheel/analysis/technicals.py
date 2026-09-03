"""Technical indicator computation for underlying stocks.

Implements RSI, EMA, ATR, and Bollinger Bands using pure pandas/numpy
(no pandas-ta dependency, which requires numba).
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from alphawheel.data.models import Momentum, TechnicalSnapshot, Trend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure pandas/numpy technical indicator implementations
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=length, adjust=False).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()


def _bbands(
    close: pd.Series, length: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands. Returns (upper, middle, lower)."""
    middle = close.rolling(window=length).mean()
    rolling_std = close.rolling(window=length).std()
    upper = middle + std * rolling_std
    lower = middle - std * rolling_std
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_technicals(symbol: str, bars_df: pd.DataFrame) -> TechnicalSnapshot:
    """Compute technical indicators from historical OHLCV bars.

    Args:
        symbol: Stock ticker symbol.
        bars_df: DataFrame with columns: open, high, low, close, volume.
                 Must have at least 20 rows for basic indicators.

    Returns:
        TechnicalSnapshot with all computed indicators.
    """
    df = bars_df.copy()

    # Ensure column names are lowercase
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 20:
        logger.warning(f"{symbol}: Insufficient bars ({len(df)}) for technical analysis")
        return TechnicalSnapshot(symbol=symbol, price=float(df["close"].iloc[-1]))

    current_price = float(df["close"].iloc[-1])

    # RSI (14)
    rsi_series = _rsi(df["close"], 14)
    rsi_14 = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

    # EMAs
    ema_20 = _safe_ema(df["close"], 20)
    ema_50 = _safe_ema(df["close"], 50)
    ema_200 = _safe_ema(df["close"], 200)

    # ATR (14)
    atr_series = _atr(df["high"], df["low"], df["close"], 14)
    atr_14 = float(atr_series.iloc[-1]) if not atr_series.empty and pd.notna(atr_series.iloc[-1]) else None

    # Bollinger Bands (20, 2σ)
    bb_upper, bb_middle, bb_lower = None, None, None
    if len(df) >= 20:
        upper, middle, lower = _bbands(df["close"], 20, 2.0)
        if pd.notna(upper.iloc[-1]):
            bb_upper = float(upper.iloc[-1])
            bb_middle = float(middle.iloc[-1])
            bb_lower = float(lower.iloc[-1])

    # Classify trend
    trend = _classify_trend(current_price, ema_50, ema_200)

    # Classify momentum
    momentum = _classify_momentum(rsi_14)

    return TechnicalSnapshot(
        symbol=symbol,
        price=current_price,
        rsi_14=rsi_14,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_200=ema_200,
        atr_14=atr_14,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        trend=trend,
        momentum=momentum,
    )


def _safe_ema(series: pd.Series, length: int) -> Optional[float]:
    """Safely compute EMA, returning None if insufficient data."""
    if len(series) < length:
        return None
    result = _ema(series, length)
    val = result.iloc[-1]
    return float(val) if pd.notna(val) else None


def _classify_trend(price: float, ema_50: Optional[float], ema_200: Optional[float]) -> Trend:
    """Classify market trend based on price vs EMAs."""
    if ema_50 is None or ema_200 is None:
        return Trend.NEUTRAL
    if price > ema_50 > ema_200:
        return Trend.BULLISH
    elif price < ema_50 < ema_200:
        return Trend.BEARISH
    return Trend.NEUTRAL


def _classify_momentum(rsi: Optional[float]) -> Momentum:
    """Classify momentum based on RSI."""
    if rsi is None:
        return Momentum.NEUTRAL
    if rsi < 35:
        return Momentum.OVERSOLD
    elif rsi > 65:
        return Momentum.OVERBOUGHT
    return Momentum.NEUTRAL
