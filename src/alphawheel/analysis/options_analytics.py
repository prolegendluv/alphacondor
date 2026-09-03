"""Options analytics: IV Rank, Black-Scholes Greeks, and contract selection."""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from alphawheel.data.models import OptionContractInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Black-Scholes Greeks (replaces py-vollib-vectorized for Python 3.14 compat)
# ---------------------------------------------------------------------------

def _d1(S: float, K: float, t: float, r: float, sigma: float) -> float:
    """Compute d1 in the Black-Scholes formula."""
    return (math.log(S / K) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))


def _d2(S: float, K: float, t: float, r: float, sigma: float) -> float:
    """Compute d2 in the Black-Scholes formula."""
    return _d1(S, K, t, r, sigma) - sigma * math.sqrt(t)


def bs_price(
    S: float, K: float, t: float, r: float, sigma: float, flag: str = "c"
) -> float:
    """Black-Scholes option price.

    Args:
        S: Underlying price.  K: Strike price.  t: Time to expiry in years.
        r: Risk-free rate.  sigma: Implied volatility.  flag: 'c' for call, 'p' for put.
    """
    if t <= 0 or sigma <= 0:
        return max(0.0, (S - K) if flag == "c" else (K - S))
    d1 = _d1(S, K, t, r, sigma)
    d2 = _d2(S, K, t, r, sigma)
    if flag == "c":
        return S * norm.cdf(d1) - K * math.exp(-r * t) * norm.cdf(d2)
    else:
        return K * math.exp(-r * t) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(
    S: float, K: float, t: float, r: float, sigma: float, flag: str = "c"
) -> dict[str, float]:
    """Compute Black-Scholes Greeks.

    Returns dict with keys: delta, gamma, theta, vega, rho.
    """
    if t <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1 = _d1(S, K, t, r, sigma)
    d2 = _d2(S, K, t, r, sigma)
    sqrt_t = math.sqrt(t)
    pdf_d1 = norm.pdf(d1)
    exp_rt = math.exp(-r * t)

    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t / 100.0  # Per 1% IV move

    if flag == "c":
        delta = norm.cdf(d1)
        theta = (-(S * pdf_d1 * sigma) / (2 * sqrt_t) - r * K * exp_rt * norm.cdf(d2)) / 365.0
        rho = K * t * exp_rt * norm.cdf(d2) / 100.0
    else:
        delta = norm.cdf(d1) - 1.0
        theta = (-(S * pdf_d1 * sigma) / (2 * sqrt_t) + r * K * exp_rt * norm.cdf(-d2)) / 365.0
        rho = -K * t * exp_rt * norm.cdf(-d2) / 100.0

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def bs_implied_volatility(
    price: float, S: float, K: float, t: float, r: float, flag: str = "c",
    tol: float = 1e-6, max_iter: int = 100,
) -> Optional[float]:
    """Compute implied volatility using Newton-Raphson method.

    Returns IV or None if convergence fails.
    """
    if price <= 0 or t <= 0:
        return None

    # Initial guess using Brenner-Subrahmanyam approximation
    sigma = math.sqrt(2 * math.pi / t) * price / S

    for _ in range(max_iter):
        try:
            bs_p = bs_price(S, K, t, r, sigma, flag)
            vega_raw = S * norm.pdf(_d1(S, K, t, r, sigma)) * math.sqrt(t)
            if vega_raw < 1e-12:
                break
            sigma -= (bs_p - price) / vega_raw
            if sigma <= 0:
                sigma = 0.001
            if abs(bs_p - price) < tol:
                return sigma
        except (ValueError, ZeroDivisionError, OverflowError):
            break

    return sigma if 0.001 < sigma < 5.0 else None


def compute_iv_rank(
    current_iv: float,
    iv_history: list[float],
) -> float:
    """Compute IV Rank as a percentile of historical IV.

    IV Rank = (Current IV - 52-week Min IV) / (52-week Max IV - 52-week Min IV) * 100

    Args:
        current_iv: Current implied volatility.
        iv_history: List of historical IV values (ideally 252 trading days).

    Returns:
        IV Rank as a percentage (0-100).
    """
    if not iv_history or len(iv_history) < 5:
        logger.warning("Insufficient IV history for IV Rank computation")
        return 50.0  # Default to median

    min_iv = min(iv_history)
    max_iv = max(iv_history)

    if max_iv == min_iv:
        return 50.0

    iv_rank = ((current_iv - min_iv) / (max_iv - min_iv)) * 100.0
    return max(0.0, min(100.0, iv_rank))


def compute_iv_percentile(
    current_iv: float,
    iv_history: list[float],
) -> float:
    """Compute IV Percentile - % of days where IV was lower than current.

    Args:
        current_iv: Current implied volatility.
        iv_history: List of historical IV values.

    Returns:
        IV Percentile as a percentage (0-100).
    """
    if not iv_history:
        return 50.0

    days_below = sum(1 for iv in iv_history if iv < current_iv)
    return (days_below / len(iv_history)) * 100.0


def select_optimal_contract(
    contracts: list[OptionContractInfo],
    target_delta: float = 0.25,
    max_bid_ask_spread_pct: float = 0.15,
) -> Optional[OptionContractInfo]:
    """Select the best option contract closest to target delta.

    Selection criteria:
    1. Filter out contracts with excessive bid-ask spreads
    2. Filter out contracts with missing/zero delta
    3. Sort by proximity to target delta
    4. Return the best match

    Args:
        contracts: List of option contracts with Greeks.
        target_delta: Target absolute delta value.
        max_bid_ask_spread_pct: Max acceptable bid-ask spread as % of mid price.

    Returns:
        Best matching contract or None if no suitable contracts found.
    """
    if not contracts:
        return None

    viable = []
    for c in contracts:
        # Must have a valid delta
        if c.delta is None:
            continue

        # Check bid-ask spread
        if c.mid > 0:
            spread_pct = (c.ask - c.bid) / c.mid
            if spread_pct > max_bid_ask_spread_pct:
                continue

        # Must have reasonable premium (at least $0.30 / $30 per contract)
        if c.bid < 0.30:
            continue

        viable.append(c)

    if not viable:
        logger.warning("No viable contracts found after filtering")
        return None

    # Sort by proximity to target delta (use absolute delta)
    viable.sort(key=lambda c: abs(abs(c.delta) - target_delta))

    best = viable[0]
    logger.info(
        f"Selected contract: {best.symbol} | Strike: ${best.strike} | "
        f"Delta: {best.delta:.3f} | Bid: ${best.bid} | Ask: ${best.ask} | "
        f"DTE: {best.dte}"
    )
    return best


def rank_contracts_for_display(
    contracts: list[OptionContractInfo],
    target_delta: float = 0.25,
    top_n: int = 5,
) -> list[OptionContractInfo]:
    """Rank and return top N contracts for LLM context."""
    if not contracts:
        return []

    # Filter to contracts with delta
    with_delta = [c for c in contracts if c.delta is not None and c.bid >= 0.05]

    # Sort by proximity to target delta
    with_delta.sort(key=lambda c: abs(abs(c.delta) - target_delta))

    return with_delta[:top_n]
