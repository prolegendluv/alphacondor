"""Wheel Strategy state machine.

Manages the lifecycle of each underlying through the Wheel cycle:
IDLE → CSP_OPEN → (assignment) → SHARES_HELD → CC_OPEN → (called away) → IDLE
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.models import (
    MarketContext,
    OptionContractInfo,
    OptionTradeSignal,
    PortfolioState,
    PositionInfo,
    TradeAction,
    WheelPhase,
    WheelState,
)

logger = logging.getLogger(__name__)


class WheelManager:
    """Manages Wheel Strategy state for multiple underlyings."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self._states: dict[str, WheelState] = {}

    def get_state(self, symbol: str) -> WheelState:
        """Get or create wheel state for a symbol."""
        if symbol not in self._states:
            self._states[symbol] = WheelState(underlying=symbol)
        return self._states[symbol]

    def set_state(self, symbol: str, state: WheelState) -> None:
        """Update wheel state for a symbol."""
        state.last_updated = datetime.utcnow()
        self._states[symbol] = state

    def get_all_states(self) -> dict[str, WheelState]:
        """Return all tracked wheel states."""
        return dict(self._states)

    def count_active_positions(self) -> int:
        """Count positions that are not IDLE."""
        return sum(
            1 for s in self._states.values()
            if s.phase != WheelPhase.IDLE
        )

    def sync_with_positions(self, portfolio: PortfolioState) -> None:
        """Synchronize wheel states with actual broker positions.

        Detects assignments (put assigned = now holding shares)
        and calls exercised (shares called away).
        """
        # Build lookup of current positions by symbol root
        equity_positions: dict[str, PositionInfo] = {}
        option_positions: dict[str, PositionInfo] = {}

        for pos in portfolio.positions:
            if pos.asset_class == "us_equity":
                equity_positions[pos.symbol] = pos
            elif pos.asset_class == "us_option":
                # Extract underlying from OCC symbol (first chars before date)
                underlying = self._extract_underlying(pos.symbol)
                option_positions[pos.symbol] = pos

        for symbol, state in self._states.items():
            equity_pos = equity_positions.get(symbol)
            shares = int(equity_pos.qty) if equity_pos else 0

            if state.phase == WheelPhase.CSP_OPEN:
                # Check if put was assigned (now holding shares)
                if shares >= 100 and state.current_option_symbol:
                    if state.current_option_symbol not in option_positions:
                        logger.info(f"{symbol}: PUT ASSIGNED! Now holding {shares} shares.")
                        state.phase = WheelPhase.SHARES_HELD
                        state.shares_held = shares
                        # Cost basis = strike price - premium collected
                        if state.current_option_entry_price:
                            state.cost_basis = (
                                state.cost_basis - state.current_option_entry_price * 100
                            )
                        state.current_option_symbol = None
                        state.current_option_entry_price = None

            elif state.phase == WheelPhase.CC_OPEN:
                # Check if call was assigned (shares called away)
                if shares < 100 and state.current_option_symbol:
                    if state.current_option_symbol not in option_positions:
                        logger.info(f"{symbol}: CALL ASSIGNED! Shares called away.")
                        state.phase = WheelPhase.IDLE
                        state.shares_held = 0
                        state.current_option_symbol = None
                        state.current_option_entry_price = None

            elif state.phase == WheelPhase.SHARES_HELD:
                state.shares_held = shares

            # Update shares count
            if equity_pos:
                state.shares_held = shares

            state.last_updated = datetime.utcnow()

    def should_manage_position(
        self,
        state: WheelState,
        option_position: Optional[PositionInfo],
    ) -> Optional[TradeAction]:
        """Check if an active option position needs management.

        Returns the recommended management action or None.
        """
        if state.phase not in (WheelPhase.CSP_OPEN, WheelPhase.CC_OPEN):
            return None

        if option_position is None:
            return None

        # Rule 1: 50% Profit Target
        if option_position.unrealized_plpc >= self.settings.profit_target_pct:
            logger.info(
                f"{state.underlying}: 50% profit target reached "
                f"(P&L%: {option_position.unrealized_plpc:.1%})"
            )
            return TradeAction.BUY_TO_CLOSE

        # Rule 2: Stop Loss
        if (
            state.current_option_entry_price
            and option_position.unrealized_pl < 0
        ):
            max_loss = state.current_option_entry_price * 100 * self.settings.max_loss_multiplier
            if abs(option_position.unrealized_pl) >= max_loss:
                logger.warning(
                    f"{state.underlying}: STOP LOSS triggered! "
                    f"Loss: ${abs(option_position.unrealized_pl):.2f} >= ${max_loss:.2f}"
                )
                return TradeAction.EXIT

        # Rule 3: 21 DTE Management
        if state.current_option_symbol:
            dte = self._get_dte_from_symbol(state.current_option_symbol)
            if dte is not None and dte <= self.settings.management_dte:
                logger.info(
                    f"{state.underlying}: 21 DTE management threshold reached (DTE: {dte})"
                )
                return TradeAction.ROLL

        return None

    def on_order_filled(
        self,
        symbol: str,
        action: TradeAction,
        option_symbol: str,
        fill_price: float,
        qty: int,
    ) -> None:
        """Update wheel state after an order fill."""
        state = self.get_state(symbol)

        if action == TradeAction.SELL_PUT:
            state.phase = WheelPhase.CSP_OPEN
            state.current_option_symbol = option_symbol
            state.current_option_entry_price = fill_price
            state.premiums_collected += fill_price * 100 * qty
            state.entry_date = date.today()
            # Set cost basis to strike price
            strike = self._extract_strike_from_symbol(option_symbol)
            if strike:
                state.cost_basis = strike
            logger.info(f"{symbol}: CSP opened - {option_symbol} @ ${fill_price}")

        elif action == TradeAction.SELL_CALL:
            state.phase = WheelPhase.CC_OPEN
            state.current_option_symbol = option_symbol
            state.current_option_entry_price = fill_price
            state.premiums_collected += fill_price * 100 * qty
            state.entry_date = date.today()
            logger.info(f"{symbol}: CC opened - {option_symbol} @ ${fill_price}")

        elif action in (TradeAction.BUY_TO_CLOSE, TradeAction.EXIT):
            if state.phase == WheelPhase.CSP_OPEN:
                state.phase = WheelPhase.IDLE
            elif state.phase == WheelPhase.CC_OPEN:
                state.phase = WheelPhase.SHARES_HELD
            state.current_option_symbol = None
            state.current_option_entry_price = None
            logger.info(f"{symbol}: Position closed")

        state.last_updated = datetime.utcnow()
        self.set_state(symbol, state)

    @staticmethod
    def _extract_underlying(occ_symbol: str) -> str:
        """Extract underlying ticker from OCC option symbol."""
        # OCC format: AAPL250919C00220000
        # Find where digits start for the date portion
        for i, c in enumerate(occ_symbol):
            if c.isdigit():
                return occ_symbol[:i]
        return occ_symbol

    @staticmethod
    def _extract_strike_from_symbol(occ_symbol: str) -> Optional[float]:
        """Extract strike price from OCC option symbol."""
        try:
            # Last 8 chars are strike * 1000
            strike_str = occ_symbol[-8:]
            return float(strike_str) / 1000.0
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _get_dte_from_symbol(occ_symbol: str) -> Optional[int]:
        """Extract DTE from OCC option symbol."""
        try:
            # Extract date portion (YYMMDD) - starts after underlying letters
            for i, c in enumerate(occ_symbol):
                if c.isdigit():
                    date_str = occ_symbol[i:i+6]
                    exp_date = datetime.strptime(date_str, "%y%m%d").date()
                    return (exp_date - date.today()).days
            return None
        except (ValueError, IndexError):
            return None
