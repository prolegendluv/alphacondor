"""LLM-powered market analysis and trade decision engine.

The LLM acts as an advisor: it analyzes market context and recommends trades
using structured JSON output. All recommendations are validated by the
deterministic risk manager before execution.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import ValidationError

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.models import (
    MarketContext,
    OptionTradeSignal,
    TradeAction,
    WheelPhase,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are AlphaWheel, an expert quantitative options trading agent managing
a Wheel Strategy portfolio. Your job is to analyze market data and make trading decisions.

## WHEEL STRATEGY RULES

### Phase 1 - Cash-Secured Put (CSP):
- When NO shares are held, sell an OTM put to collect premium
- Target: 0.20-0.30 delta puts, 30-45 DTE
- Only enter when IV Rank >= 25% and trend is not strongly bearish
- If assigned, acquire shares at strike price (move to Phase 2)

### Phase 2 - Covered Call (CC):
- When 100+ shares ARE held, sell an OTM call against them
- Target: 0.20-0.30 delta calls, 30-45 DTE
- Strike should be >= cost basis to ensure profit if called away
- If called away, sell shares at strike (return to Phase 1)

### Position Management:
- CLOSE at 50% profit: If option has lost 50% of its value, buy to close
- ROLL at 21 DTE: If still open at 21 DTE, close and open new position
- STOP LOSS: Close if loss exceeds 200% of premium received
- NEVER hold through earnings (expiration must not cross earnings date)

## YOUR OUTPUT
You MUST output ONLY a valid JSON object matching this exact schema:
{
    "action": "SELL_PUT" | "SELL_CALL" | "BUY_TO_CLOSE" | "ROLL" | "HOLD" | "EXIT",
    "underlying": "SYMBOL",
    "contract_symbol": "OCC_SYMBOL or null",
    "target_strike": <float or null>,
    "target_dte": <int or null>,
    "target_delta": <float or null>,
    "limit_price": <float or null>,
    "confidence": <float 0.0-1.0>,
    "rationale": "Clear explanation of your reasoning",
    "risk_factors": ["list of identified risks"]
}

## IMPORTANT GUIDELINES
- Be conservative. When uncertain, output "HOLD" with lower confidence.
- Always explain your reasoning clearly in the rationale field.
- Consider ALL provided data: technicals, IV rank, Greeks, sentiment, portfolio state.
- Factor in current market regime (trend + momentum).
- Risk factors should list specific concerns (earnings proximity, high IV, trend reversal, etc.).
"""


class LLMReasoner:
    """Generates structured trade decisions using an LLM."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the Gemini client."""
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.settings.google_api_key)
            except ImportError:
                logger.error("google-genai not installed")
                raise
        return self._client

    def generate_decision(
        self,
        context: MarketContext,
    ) -> Optional[OptionTradeSignal]:
        """Analyze market context and generate a trade decision.

        Args:
            context: Full market context including technicals, options, portfolio.

        Returns:
            Structured OptionTradeSignal or None if generation fails.
        """
        if not self.settings.google_api_key:
            logger.warning("No Google API key, using rule-based fallback")
            return self._rule_based_fallback(context)

        user_prompt = self._build_prompt(context)

        try:
            from google.genai import types
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            )
            response = self.client.models.generate_content(
                model=self.settings.llm_model,
                contents=user_prompt,
                config=config,
            )

            raw_text = response.text.strip()
            logger.debug(f"LLM raw response: {raw_text}")

            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                raw_text = raw_text.rsplit("```", 1)[0].strip()

            signal = OptionTradeSignal.model_validate_json(raw_text)
            logger.info(
                f"LLM decision for {context.symbol}: {signal.action.value} "
                f"(confidence: {signal.confidence:.2f})"
            )
            return signal

        except ValidationError as e:
            logger.error(f"LLM output validation failed: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"LLM output JSON parse failed: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM decision generation failed: {e}")
            return self._rule_based_fallback(context)

    def _build_prompt(self, context: MarketContext) -> str:
        """Build the user prompt with all market context."""
        sections = []
        sections.append(f"## Market Analysis Request for {context.symbol}")
        sections.append(f"Current Price: ${context.current_price:.2f}")
        sections.append(f"Timestamp: {context.timestamp}")

        # Technicals
        t = context.technicals
        sections.append(f"\n### Technical Indicators")
        sections.append(f"- Trend: {t.trend.value.upper()}")
        sections.append(f"- Momentum: {t.momentum.value.upper()}")
        sections.append(f"- RSI(14): {t.rsi_14:.1f}" if t.rsi_14 else "- RSI(14): N/A")
        sections.append(f"- EMA(20): ${t.ema_20:.2f}" if t.ema_20 else "- EMA(20): N/A")
        sections.append(f"- EMA(50): ${t.ema_50:.2f}" if t.ema_50 else "- EMA(50): N/A")
        sections.append(f"- EMA(200): ${t.ema_200:.2f}" if t.ema_200 else "- EMA(200): N/A")
        sections.append(f"- ATR(14): ${t.atr_14:.2f}" if t.atr_14 else "- ATR(14): N/A")
        if t.bb_upper:
            sections.append(f"- Bollinger Bands: [{t.bb_lower:.2f}, {t.bb_middle:.2f}, {t.bb_upper:.2f}]")

        # IV Rank
        if context.iv_rank is not None:
            sections.append(f"\n### Implied Volatility")
            sections.append(f"- IV Rank: {context.iv_rank:.1f}%")

        # Top Contracts
        if context.top_contracts:
            sections.append(f"\n### Available Option Contracts (sorted by delta proximity)")
            for c in context.top_contracts[:5]:
                delta_str = f"{c.delta:.3f}" if c.delta else "N/A"
                iv_str = f"{c.implied_volatility:.1%}" if c.implied_volatility else "N/A"
                sections.append(
                    f"- {c.symbol} | Strike: ${c.strike:.2f} | DTE: {c.dte} | "
                    f"Delta: {delta_str} | Bid: ${c.bid:.2f} | Ask: ${c.ask:.2f} | IV: {iv_str}"
                )

        # Portfolio State
        if context.portfolio:
            p = context.portfolio
            sections.append(f"\n### Portfolio State")
            sections.append(f"- Equity: ${p.equity:.2f}")
            sections.append(f"- Cash: ${p.cash:.2f}")
            sections.append(f"- Buying Power: ${p.buying_power:.2f}")
            sections.append(f"- Total P&L: ${p.total_pl:.2f}")
            sections.append(f"- Open Positions: {len(p.positions)}")

        # Wheel State
        if context.wheel_state:
            ws = context.wheel_state
            sections.append(f"\n### Current Wheel State for {context.symbol}")
            sections.append(f"- Phase: {ws.phase.value}")
            sections.append(f"- Shares Held: {ws.shares_held}")
            sections.append(f"- Cost Basis: ${ws.cost_basis:.2f}")
            sections.append(f"- Premiums Collected: ${ws.premiums_collected:.2f}")
            if ws.current_option_symbol:
                sections.append(f"- Active Option: {ws.current_option_symbol}")
                sections.append(f"- Option Entry Price: ${ws.current_option_entry_price:.2f}" if ws.current_option_entry_price else "")

        # Sentiment
        if context.sentiment_score is not None:
            sections.append(f"\n### Sentiment")
            sections.append(f"- Score: {context.sentiment_score:.2f} (-1=bearish, +1=bullish)")
        if context.news_summary:
            sections.append(f"- News: {context.news_summary}")

        # Earnings
        if context.earnings_date:
            sections.append(f"\n### Earnings")
            sections.append(f"- Next Earnings Date: {context.earnings_date}")

        sections.append("\n### Instructions")
        sections.append("Analyze the above data and output your trading decision as a JSON object.")

        return "\n".join(sections)

    def _rule_based_fallback(self, context: MarketContext) -> OptionTradeSignal:
        """Simple rule-based fallback when LLM is unavailable."""
        ws = context.wheel_state

        # Default to HOLD
        action = TradeAction.HOLD
        confidence = 0.5
        rationale = "Rule-based fallback (LLM unavailable). "
        risk_factors = ["LLM unavailable - using simplified rules"]

        if ws is None or ws.phase == WheelPhase.IDLE:
            # Check if conditions are right for a CSP
            t = context.technicals
            if (
                t.trend != "bearish"
                and t.rsi_14 is not None
                and 30 <= t.rsi_14 <= 65
                and context.iv_rank is not None
                and context.iv_rank >= 25
            ):
                action = TradeAction.SELL_PUT
                confidence = 0.72
                rationale += f"Trend is {t.trend.value}, RSI={t.rsi_14:.1f}, IV Rank={context.iv_rank:.1f}%. Conditions favor selling a put."
            else:
                rationale += "Conditions not favorable for entry."

        elif ws.phase == WheelPhase.SHARES_HELD:
            action = TradeAction.SELL_CALL
            confidence = 0.75
            rationale += f"Holding {ws.shares_held} shares. Selling covered call."

        return OptionTradeSignal(
            action=action,
            underlying=context.symbol,
            confidence=confidence,
            rationale=rationale,
            risk_factors=risk_factors,
            target_delta=context.technicals.price * 0 + 0.25,  # Always target 0.25 delta
        )
