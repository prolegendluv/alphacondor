"""Pydantic data models for AlphaWheel internal data flow."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Trend(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class Momentum(str, Enum):
    OVERSOLD = "oversold"
    NEUTRAL = "neutral"
    OVERBOUGHT = "overbought"


class WheelPhase(str, Enum):
    IDLE = "idle"
    CSP_OPEN = "csp_open"
    SHARES_HELD = "shares_held"
    CC_OPEN = "cc_open"
    ROLLING = "rolling"


class TradeAction(str, Enum):
    SELL_PUT = "SELL_PUT"
    SELL_CALL = "SELL_CALL"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    ROLL = "ROLL"
    HOLD = "HOLD"
    EXIT = "EXIT"


class TechnicalSnapshot(BaseModel):
    """Technical analysis indicators for an underlying."""
    symbol: str
    price: float
    rsi_14: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    atr_14: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    trend: Trend = Trend.NEUTRAL
    momentum: Momentum = Momentum.NEUTRAL
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class OptionContractInfo(BaseModel):
    """Summarized option contract data."""
    symbol: str  # OCC symbol e.g. AAPL250919C00220000
    underlying: str
    contract_type: str  # 'call' or 'put'
    strike: float
    expiration: date
    dte: int
    bid: float
    ask: float
    mid: float
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None


class PositionInfo(BaseModel):
    """Individual position information."""
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    asset_class: str  # 'us_equity' or 'us_option'
    side: str  # 'long' or 'short'


class PortfolioState(BaseModel):
    """Current portfolio snapshot."""
    equity: float
    cash: float
    buying_power: float
    options_buying_power: float = 0.0
    positions: list[PositionInfo] = Field(default_factory=list)
    open_orders: int = 0
    total_pl: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WheelState(BaseModel):
    """Tracks the state of a single underlying in the Wheel cycle."""
    underlying: str
    phase: WheelPhase = WheelPhase.IDLE
    cost_basis: float = 0.0
    premiums_collected: float = 0.0
    shares_held: int = 0
    current_option_symbol: Optional[str] = None
    current_option_entry_price: Optional[float] = None
    entry_date: Optional[date] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class OptionTradeSignal(BaseModel):
    """Structured trade signal output from LLM reasoner."""
    action: TradeAction
    underlying: str
    contract_symbol: Optional[str] = None
    target_strike: Optional[float] = None
    target_dte: Optional[int] = None
    target_delta: Optional[float] = None
    limit_price: Optional[float] = None
    quantity: int = Field(1, ge=1, description="Number of option contracts")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    risk_factors: list[str] = Field(default_factory=list)


class MarketContext(BaseModel):
    """Full market context passed to the LLM for reasoning."""
    symbol: str
    current_price: float
    technicals: TechnicalSnapshot
    iv_rank: Optional[float] = None
    top_contracts: list[OptionContractInfo] = Field(default_factory=list)
    portfolio: Optional[PortfolioState] = None
    wheel_state: Optional[WheelState] = None
    news_summary: Optional[str] = None
    sentiment_score: Optional[float] = None
    earnings_date: Optional[date] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RiskGateResult(BaseModel):
    """Result of risk validation."""
    passed: bool
    signal: OptionTradeSignal
    rejections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
