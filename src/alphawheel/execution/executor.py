"""Order execution engine for Alpaca Trading API.

Handles order submission, cancellation, and position management.
All orders use limit pricing for better fills.
"""

from __future__ import annotations

import logging
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderSide,
    OrderStatus,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
)

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.models import OptionTradeSignal, TradeAction

logger = logging.getLogger(__name__)


class OrderExecutor:
    """Executes option orders via the Alpaca Trading API."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self.client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )

    def execute_signal(self, signal: OptionTradeSignal) -> Optional[dict]:
        """Execute a validated trade signal.

        Args:
            signal: The validated trade signal to execute.

        Returns:
            Order details dict or None if execution failed.
        """
        if signal.action == TradeAction.HOLD:
            logger.info(f"{signal.underlying}: HOLD - no action taken")
            return None

        if not signal.contract_symbol:
            logger.error(f"{signal.underlying}: No contract symbol in signal")
            return None

        try:
            if signal.action == TradeAction.SELL_PUT:
                return self._sell_to_open(signal)
            elif signal.action == TradeAction.SELL_CALL:
                return self._sell_to_open(signal)
            elif signal.action == TradeAction.BUY_TO_CLOSE:
                return self._buy_to_close(signal)
            elif signal.action == TradeAction.EXIT:
                return self._buy_to_close(signal)
            elif signal.action == TradeAction.ROLL:
                return self._buy_to_close(signal)  # First leg: close current
            else:
                logger.warning(f"Unhandled action: {signal.action}")
                return None

        except Exception as e:
            logger.error(f"Order execution failed for {signal.underlying}: {e}")
            return None

    def _sell_to_open(self, signal: OptionTradeSignal) -> Optional[dict]:
        """Submit a Sell-to-Open order (CSP or CC)."""
        if not signal.limit_price or signal.limit_price <= 0:
            logger.error(f"Invalid limit price for STO: {signal.limit_price}")
            return None

        qty = max(1, signal.quantity or 1)
        order_request = LimitOrderRequest(
            symbol=signal.contract_symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(signal.limit_price, 2),
            position_intent=PositionIntent.SELL_TO_OPEN,
        )

        order = self.client.submit_order(order_data=order_request)
        logger.info(
            f"STO Order submitted: {order.id} | {signal.contract_symbol} | "
            f"Qty: {qty} | ${signal.limit_price:.2f} | Status: {order.status}"
        )

        return {
            "order_id": str(order.id),
            "symbol": signal.contract_symbol,
            "side": "sell",
            "intent": "sell_to_open",
            "qty": qty,
            "limit_price": signal.limit_price,
            "status": str(order.status),
        }

    def _buy_to_close(self, signal: OptionTradeSignal) -> Optional[dict]:
        """Submit a Buy-to-Close order."""
        # For BTC, we can use market order or a limit slightly above ask
        limit_price = signal.limit_price
        if not limit_price or limit_price <= 0:
            # Try to close at market via the position close endpoint
            try:
                self.client.close_position(signal.contract_symbol)
                logger.info(f"Position closed via close_position: {signal.contract_symbol}")
                return {
                    "order_id": "position_close",
                    "symbol": signal.contract_symbol,
                    "side": "buy",
                    "intent": "buy_to_close",
                    "qty": 1,
                    "limit_price": 0,
                    "status": "submitted",
                }
            except Exception as e:
                logger.error(f"Failed to close position {signal.contract_symbol}: {e}")
                return None

        order_request = LimitOrderRequest(
            symbol=signal.contract_symbol,
            qty=1,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            position_intent=PositionIntent.BUY_TO_CLOSE,
        )

        order = self.client.submit_order(order_data=order_request)
        logger.info(
            f"BTC Order submitted: {order.id} | {signal.contract_symbol} | "
            f"${limit_price:.2f} | Status: {order.status}"
        )

        return {
            "order_id": str(order.id),
            "symbol": signal.contract_symbol,
            "side": "buy",
            "intent": "buy_to_close",
            "qty": 1,
            "limit_price": limit_price,
            "status": str(order.status),
        }

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        orders = self.client.get_orders(filter=req)
        return [
            {
                "order_id": str(o.id),
                "symbol": o.symbol,
                "side": str(o.side),
                "type": str(o.type),
                "status": str(o.status),
                "limit_price": str(o.limit_price) if o.limit_price else None,
                "filled_avg_price": str(o.filled_avg_price) if o.filled_avg_price else None,
            }
            for o in orders
        ]

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count of cancelled orders."""
        try:
            result = self.client.cancel_orders()
            count = len(result) if result else 0
            logger.info(f"Cancelled {count} open orders")
            return count
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return 0
