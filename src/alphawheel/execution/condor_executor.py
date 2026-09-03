"""Multi-leg Iron Condor order execution engine for Alpaca Trading API."""

from __future__ import annotations

import logging
from typing import Optional, Dict

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce, OrderStatus
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
from alpaca.common.exceptions import APIError

from alphawheel.config import AlphaWheelSettings
from alphawheel.strategy.condor import IronCondorPosition, CondorStatus

logger = logging.getLogger(__name__)


class CondorExecutor:
    """Handles multi-leg Iron Condor order execution on Alpaca."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self.client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )

    def _submit_leg(
        self,
        symbol: str,
        side: OrderSide,
        intent: PositionIntent,
        qty: int,
        limit_price: Optional[float] = None,
    ) -> Optional[str]:
        """Submit a single leg limit order (or market if limit_price is not provided)."""
        try:
            if limit_price and limit_price > 0:
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(limit_price, 2),
                    position_intent=intent,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    position_intent=intent,
                )

            order = self.client.submit_order(req)
            logger.info(
                f"Submitted leg {symbol} | side={side.name} | intent={intent.name} | "
                f"qty={qty} | price={limit_price} | id={order.id}"
            )
            return str(order.id)
        except APIError as e:
            logger.error(f"Alpaca API Error submitting leg {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error submitting leg {symbol}: {e}")
            return None

    def open_condor(self, position: IronCondorPosition) -> Dict:
        """Submit all 4 legs of an iron condor to open the position.

        Returns:
            Dictionary with 'success' boolean and 'order_ids' mapping.
        """
        logger.info(f"Opening Iron Condor for {position.underlying} ({position.quantity} spreads)")

        order_ids = {}

        # 1. Long put: BUY_TO_OPEN
        order_ids["put_long"] = self._submit_leg(
            symbol=position.put_long.symbol,
            side=OrderSide.BUY,
            intent=PositionIntent.BUY_TO_OPEN,
            qty=position.quantity,
            limit_price=position.put_long.fill_price or position.put_long.ask,
        )

        # 2. Short put: SELL_TO_OPEN
        order_ids["put_short"] = self._submit_leg(
            symbol=position.put_short.symbol,
            side=OrderSide.SELL,
            intent=PositionIntent.SELL_TO_OPEN,
            qty=position.quantity,
            limit_price=position.put_short.fill_price or position.put_short.bid,
        )

        # 3. Short call: SELL_TO_OPEN
        order_ids["call_short"] = self._submit_leg(
            symbol=position.call_short.symbol,
            side=OrderSide.SELL,
            intent=PositionIntent.SELL_TO_OPEN,
            qty=position.quantity,
            limit_price=position.call_short.fill_price or position.call_short.bid,
        )

        # 4. Long call: BUY_TO_OPEN
        order_ids["call_long"] = self._submit_leg(
            symbol=position.call_long.symbol,
            side=OrderSide.BUY,
            intent=PositionIntent.BUY_TO_OPEN,
            qty=position.quantity,
            limit_price=position.call_long.fill_price or position.call_long.ask,
        )

        # Update position leg order IDs
        position.put_long.order_id = order_ids["put_long"]
        position.put_short.order_id = order_ids["put_short"]
        position.call_short.order_id = order_ids["call_short"]
        position.call_long.order_id = order_ids["call_long"]

        success = all(oid is not None for oid in order_ids.values())
        if success:
            position.status = CondorStatus.OPEN
            logger.info(f"All 4 legs submitted successfully for condor {position.id[:8]}")
        else:
            logger.warning(
                f"Partial fill or failure opening condor {position.id[:8]}: {order_ids}"
            )

        return {
            "success": success,
            "order_ids": order_ids,
        }

    def close_condor(self, position: IronCondorPosition) -> Dict:
        """Submit all 4 legs of an iron condor to close the position.

        Returns:
            Dictionary with 'success' boolean and 'order_ids' mapping.
        """
        logger.info(f"Closing Iron Condor for {position.underlying} ({position.id[:8]})")

        order_ids = {}

        # 1. Long put: SELL_TO_CLOSE (or close_position)
        order_ids["put_long"] = self._close_leg(position.put_long.symbol, OrderSide.SELL, PositionIntent.SELL_TO_CLOSE, position.quantity)

        # 2. Short put: BUY_TO_CLOSE
        order_ids["put_short"] = self._close_leg(position.put_short.symbol, OrderSide.BUY, PositionIntent.BUY_TO_CLOSE, position.quantity)

        # 3. Short call: BUY_TO_CLOSE
        order_ids["call_short"] = self._close_leg(position.call_short.symbol, OrderSide.BUY, PositionIntent.BUY_TO_CLOSE, position.quantity)

        # 4. Long call: SELL_TO_CLOSE
        order_ids["call_long"] = self._close_leg(position.call_long.symbol, OrderSide.SELL, PositionIntent.SELL_TO_CLOSE, position.quantity)

        success = all(oid is not None for oid in order_ids.values())
        if success:
            position.status = CondorStatus.CLOSING
            logger.info(f"All 4 close orders submitted for condor {position.id[:8]}")
        else:
            logger.warning(f"Some close orders failed for condor {position.id[:8]}: {order_ids}")

        return {
            "success": success,
            "order_ids": order_ids,
        }

    def _close_leg(self, symbol: str, side: OrderSide, intent: PositionIntent, qty: int) -> Optional[str]:
        """Try closing via close_position first, fallback to submitting order."""
        try:
            self.client.close_position(symbol)
            logger.info(f"Closed leg via close_position: {symbol}")
            return f"closed_{symbol}"
        except Exception as e:
            logger.debug(f"close_position failed for {symbol}, falling back to order: {e}")
            return self._submit_leg(symbol, side, intent, qty)

    def get_condor_market_value(self, position: IronCondorPosition) -> Optional[float]:
        """Calculate the current market value (cost to close) per spread.

        Returns:
            Net debit per spread to close the position.
        """
        try:
            positions = self.client.get_all_positions()
            pos_dict = {p.symbol: p for p in positions}

            symbols = [
                position.put_long.symbol,
                position.put_short.symbol,
                position.call_short.symbol,
                position.call_long.symbol,
            ]

            # If none of the legs are found in open positions, return estimated value
            matched = [s for s in symbols if s in pos_dict]
            if not matched:
                logger.debug("No condor legs currently in active account positions")
                return None

            # Cost to close per share:
            # Short put: buy back at current price (positive cost)
            # Short call: buy back at current price (positive cost)
            # Long put: sell at current price (negative cost / rebate)
            # Long call: sell at current price (negative cost / rebate)
            close_cost_per_spread = 0.0
            for sym, sign in [
                (position.put_short.symbol, 1.0),
                (position.call_short.symbol, 1.0),
                (position.put_long.symbol, -1.0),
                (position.call_long.symbol, -1.0),
            ]:
                if sym in pos_dict:
                    curr_price = float(pos_dict[sym].current_price)
                    close_cost_per_spread += sign * curr_price

            return max(0.0, close_cost_per_spread)

        except Exception as e:
            logger.error(f"Error calculating condor market value: {e}")
            return None
