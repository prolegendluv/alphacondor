import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alphawheel.config import get_settings
from alphawheel.data.models import MarketContext, TechnicalSnapshot, OptionContractInfo, Trend, Momentum
from alphawheel.strategy.llm_reasoner import LLMReasoner
from alphawheel.strategy.wheel import WheelManager
from datetime import date

settings = get_settings()
reasoner = LLMReasoner(settings)
wheel_manager = WheelManager(settings)

# Mock context for fast testing
technicals = TechnicalSnapshot(
    symbol="NVDA",
    price=128.50,
    rsi_14=42.0,
    ema_20=130.0,
    ema_50=125.0,
    ema_200=115.0,
    atr_14=3.80,
    trend=Trend.BULLISH,
    momentum=Momentum.NEUTRAL
)

top_contracts = [
    OptionContractInfo(
        symbol="NVDA260930P00120000",
        underlying="NVDA",
        contract_type="put",
        strike=120.0,
        expiration=date(2026, 9, 30),
        dte=34,
        bid=2.45,
        ask=2.55,
        mid=2.50,
        delta=-0.245,
        implied_volatility=0.45
    )
]

context = MarketContext(
    symbol="NVDA",
    current_price=128.50,
    technicals=technicals,
    iv_rank=35.0,
    top_contracts=top_contracts,
    wheel_state=wheel_manager.get_state("NVDA"),
    news_summary="Nvidia sees steady enterprise demand for Blackwell architecture.",
    sentiment_score=0.40
)

print("[1] Requesting trade decision from Gemini...")
signal = reasoner.generate_decision(context)

if signal:
    print("\n[SUCCESS] Decision received:")
    print("Action:", signal.action.value)
    print("Confidence:", f"{signal.confidence:.0%}")
    print("Rationale:", signal.rationale)
    print("Risk Factors:", signal.risk_factors)
else:
    print("[ERROR] Signal generation returned None.")
