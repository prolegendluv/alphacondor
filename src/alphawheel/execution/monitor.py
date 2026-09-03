"""Real-time order fill monitoring via Alpaca WebSocket.

Monitors trade updates (fills, cancellations, rejections) and
updates the Wheel state machine accordingly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from alpaca.trading.stream import TradingStream

from alphawheel.config import AlphaWheelSettings

logger = logging.getLogger(__name__)


class FillMonitor:
    """Monitors order fills via Alpaca's WebSocket streaming API."""

    def __init__(
        self,
        settings: AlphaWheelSettings,
        on_fill: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
    ):
        self.settings = settings
        self.on_fill = on_fill
        self.on_cancel = on_cancel
        self._stream: Optional[TradingStream] = None
        self._running = False

    def _create_stream(self) -> TradingStream:
        """Create a new TradingStream instance."""
        return TradingStream(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
            paper=self.settings.alpaca_paper,
        )

    async def start(self) -> None:
        """Start monitoring trade updates."""
        if self._running:
            logger.warning("Fill monitor already running")
            return

        self._stream = self._create_stream()
        self._stream.subscribe_trade_updates(self._handle_trade_update)
        self._running = True

        logger.info("Fill monitor started - listening for trade updates")

        try:
            await self._stream._run_forever()
        except Exception as e:
            logger.error(f"Fill monitor stream error: {e}")
            self._running = False

    async def stop(self) -> None:
        """Stop the fill monitor."""
        self._running = False
        if self._stream:
            try:
                await self._stream.close()
            except Exception:
                pass
        logger.info("Fill monitor stopped")

    async def _handle_trade_update(self, data) -> None:
        """Handle incoming trade update events."""
        try:
            event = data.event
            order = data.order

            order_id = order.get("id", "unknown") if isinstance(order, dict) else getattr(order, "id", "unknown")
            symbol = order.get("symbol", "unknown") if isinstance(order, dict) else getattr(order, "symbol", "unknown")

            logger.info(f"Trade update: {event} | Order: {order_id} | Symbol: {symbol}")

            if event == "fill":
                filled_price = (
                    order.get("filled_avg_price")
                    if isinstance(order, dict)
                    else getattr(order, "filled_avg_price", None)
                )
                filled_qty = (
                    order.get("filled_qty")
                    if isinstance(order, dict)
                    else getattr(order, "filled_qty", None)
                )

                logger.info(
                    f"ORDER FILLED: {symbol} | Price: ${filled_price} | Qty: {filled_qty}"
                )

                if self.on_fill:
                    self.on_fill(
                        order_id=str(order_id),
                        symbol=symbol,
                        filled_price=float(filled_price) if filled_price else 0.0,
                        filled_qty=int(filled_qty) if filled_qty else 0,
                        raw_data=data,
                    )

            elif event in ("canceled", "rejected", "expired"):
                logger.warning(f"Order {event}: {order_id} | {symbol}")

                if self.on_cancel:
                    self.on_cancel(
                        order_id=str(order_id),
                        symbol=symbol,
                        event=event,
                        raw_data=data,
                    )

            elif event == "partial_fill":
                logger.info(f"Partial fill: {order_id} | {symbol}")

        except Exception as e:
            logger.error(f"Error handling trade update: {e}", exc_info=True)


def run_fill_monitor(
    settings: AlphaWheelSettings,
    on_fill: Optional[Callable] = None,
    on_cancel: Optional[Callable] = None,
) -> None:
    """Run the fill monitor in a background thread."""
    monitor = FillMonitor(settings, on_fill=on_fill, on_cancel=on_cancel)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(monitor.start())
    except KeyboardInterrupt:
        loop.run_until_complete(monitor.stop())
    finally:
        loop.close()
