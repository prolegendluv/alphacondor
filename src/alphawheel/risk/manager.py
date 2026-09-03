"""Deterministic risk management gates.

All trade signals from the LLM must pass through these gates before execution.
No LLM involvement in risk decisions — pure algorithmic validation.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.models import (
    OptionTradeSignal,
    PortfolioState,
    RiskGateResult,
    TradeAction,
    WheelPhase,
    WheelState,
)
from alphawheel.strategy.wheel import WheelManager

logger = logging.getLogger(__name__)


class RiskManager:
    """Validates trade signals against deterministic risk constraints."""

    def __init__(self, settings: AlphaWheelSettings, wheel_manager: WheelManager):
        self.settings = settings
        self.wheel_manager = wheel_manager

    def validate(
        self,
        signal: OptionTradeSignal,
        portfolio: PortfolioState,
        wheel_state: Optional[WheelState] = None,
    ) -> RiskGateResult:
        """Run all risk gates on a trade signal.

        Args:
            signal: The proposed trade signal from the LLM or rule engine.
            portfolio: Current portfolio state.
            wheel_state: Current wheel state for the underlying.

        Returns:
            RiskGateResult with pass/fail and rejection reasons.
        """
        rejections: list[str] = []
        warnings: list[str] = []

        # Gate 0: HOLD and EXIT always pass
        if signal.action in (TradeAction.HOLD,):
            return RiskGateResult(passed=True, signal=signal)

        # Gate 1: Confidence Floor
        if signal.confidence < self.settings.min_confidence:
            rejections.append(
                f"Confidence {signal.confidence:.2f} below minimum {self.settings.min_confidence}"
            )

        # Gate 2: Max Concurrent Positions
        if signal.action in (TradeAction.SELL_PUT, TradeAction.SELL_CALL):
            active_count = self.wheel_manager.count_active_positions()
            if active_count >= self.settings.max_concurrent_positions:
                rejections.append(
                    f"Max concurrent positions reached ({active_count}/{self.settings.max_concurrent_positions})"
                )

        # Gate 3: Buying Power (Cash-Secured Put)
        if signal.action == TradeAction.SELL_PUT and signal.target_strike:
            qty = max(1, signal.quantity or 1)
            required_collateral = signal.target_strike * 100 * qty
            if required_collateral > portfolio.cash:
                rejections.append(
                    f"Insufficient cash (${portfolio.cash:.2f}) for CSP collateral "
                    f"(${required_collateral:.2f} needed for {qty} contracts @ strike ${signal.target_strike})"
                )

            # Gate 4: Concentration Limit
            if portfolio.equity > 0:
                allocation_pct = required_collateral / portfolio.equity
                if allocation_pct > self.settings.max_ticker_allocation:
                    rejections.append(
                        f"Ticker allocation {allocation_pct:.1%} exceeds max "
                        f"{self.settings.max_ticker_allocation:.0%}"
                    )

        # Gate 5: Covered Call - Must Own Shares
        if signal.action == TradeAction.SELL_CALL:
            ws = wheel_state or self.wheel_manager.get_state(signal.underlying)
            if ws.shares_held < 100:
                rejections.append(
                    f"Cannot sell covered call: only {ws.shares_held} shares held (need 100)"
                )

        # Gate 6: Phase Consistency
        if wheel_state:
            phase_error = self._check_phase_consistency(signal, wheel_state)
            if phase_error:
                rejections.append(phase_error)

        # Gate 7: Bid-Ask Spread (warning only, not a hard rejection)
        if signal.limit_price and signal.limit_price <= 0.05:
            warnings.append("Very low limit price - may indicate illiquid contract")

        # Log results
        passed = len(rejections) == 0
        if passed:
            logger.info(
                f"Risk check PASSED for {signal.underlying} {signal.action.value} "
                f"(confidence: {signal.confidence:.2f})"
            )
        else:
            logger.warning(
                f"Risk check FAILED for {signal.underlying} {signal.action.value}: "
                f"{'; '.join(rejections)}"
            )

        if warnings:
            logger.info(f"Risk warnings: {'; '.join(warnings)}")

        return RiskGateResult(
            passed=passed,
            signal=signal,
            rejections=rejections,
            warnings=warnings,
        )

    def _check_phase_consistency(
        self,
        signal: OptionTradeSignal,
        wheel_state: WheelState,
    ) -> Optional[str]:
        """Verify the signal is consistent with the current wheel phase."""
        phase = wheel_state.phase

        if signal.action == TradeAction.SELL_PUT:
            if phase not in (WheelPhase.IDLE,):
                return f"Cannot sell put in phase {phase.value} (must be IDLE)"

        elif signal.action == TradeAction.SELL_CALL:
            if phase not in (WheelPhase.SHARES_HELD,):
                return f"Cannot sell call in phase {phase.value} (must be SHARES_HELD)"

        elif signal.action == TradeAction.BUY_TO_CLOSE:
            if phase not in (WheelPhase.CSP_OPEN, WheelPhase.CC_OPEN):
                return f"No open option to close in phase {phase.value}"

        elif signal.action == TradeAction.ROLL:
            if phase not in (WheelPhase.CSP_OPEN, WheelPhase.CC_OPEN):
                return f"No open option to roll in phase {phase.value}"

        return None
