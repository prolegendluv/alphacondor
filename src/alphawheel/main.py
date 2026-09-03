"""AlphaWheel main entry point and agent orchestrator.

Coordinates all components: data ingestion, analysis, strategy,
risk management, and execution in a scheduled loop.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from alphawheel.analysis.options_analytics import rank_contracts_for_display, select_optimal_contract
from alphawheel.analysis.sentiment import SentimentAnalyzer
from alphawheel.analysis.technicals import compute_technicals
from alphawheel.config import AlphaWheelSettings
from alphawheel.data.market_data import MarketDataService
from alphawheel.data.models import (
    MarketContext,
    OptionTradeSignal,
    TradeAction,
    WheelPhase,
)
from alphawheel.data.news import NewsService
from alphawheel.execution.executor import OrderExecutor
from alphawheel.execution.monitor import run_fill_monitor
from alphawheel.journal.trade_log import TradeJournal
from alphawheel.risk.manager import RiskManager
from alphawheel.strategy.llm_reasoner import LLMReasoner
from alphawheel.strategy.wheel import WheelManager

logger = logging.getLogger(__name__)


class AlphaWheelAgent:
    """Main agent orchestrator."""

    def __init__(self, settings: AlphaWheelSettings, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run

        # Initialize all components
        self.data_service = MarketDataService(settings)
        self.news_service = NewsService(settings)
        self.sentiment = SentimentAnalyzer(settings)
        self.wheel_manager = WheelManager(settings)
        self.risk_manager = RiskManager(settings, self.wheel_manager)
        self.executor = OrderExecutor(settings)
        self.reasoner = LLMReasoner(settings)
        self.journal = TradeJournal(settings.db_path)

        self._scheduler: Optional[BlockingScheduler] = None
        self._shutdown_event = threading.Event()

        logger.info(
            f"AlphaWheel Agent initialized | "
            f"Universe: {settings.universe} | "
            f"Dry Run: {dry_run} | "
            f"Paper: {settings.alpaca_paper}"
        )

    def run_cycle(self, force: bool = False) -> None:
        """Execute one full analysis and trading cycle."""
        logger.info("="*60)
        logger.info(f"Starting analysis cycle at {datetime.now()}")
        logger.info("="*60)

        # 1. Check if market is open
        if not force and not self.data_service.is_market_open():
            logger.info("Market is closed. Skipping cycle. (Use --force to run anyway)")
            return

        # 2. Get portfolio state
        try:
            portfolio = self.data_service.get_portfolio_state()
            self.journal.log_portfolio_snapshot(portfolio)
            logger.info(
                f"Portfolio | Equity: ${portfolio.equity:,.2f} | "
                f"Cash: ${portfolio.cash:,.2f} | P&L: ${portfolio.total_pl:,.2f}"
            )
        except Exception as e:
            logger.error(f"Failed to get portfolio state: {e}")
            return

        # 3. Sync wheel states with actual positions
        self.wheel_manager.sync_with_positions(portfolio)

        # 4. Manage existing positions first
        self._manage_existing_positions(portfolio)

        # 5. Analyze each symbol in universe
        for symbol in self.settings.universe:
            try:
                live_portfolio = self.data_service.get_portfolio_state()
                self._analyze_and_trade(symbol, live_portfolio)
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        logger.info("Analysis cycle complete")

    def _manage_existing_positions(self, portfolio) -> None:
        """Check and manage all active option positions."""
        for symbol, state in self.wheel_manager.get_all_states().items():
            if state.phase not in (WheelPhase.CSP_OPEN, WheelPhase.CC_OPEN):
                continue

            # Find the matching option position
            option_pos = None
            for pos in portfolio.positions:
                if pos.symbol == state.current_option_symbol:
                    option_pos = pos
                    break

            management_action = self.wheel_manager.should_manage_position(state, option_pos)
            if management_action and option_pos:
                logger.info(
                    f"{symbol}: Management action triggered - {management_action.value}"
                )

                signal = OptionTradeSignal(
                    action=management_action,
                    underlying=symbol,
                    contract_symbol=state.current_option_symbol,
                    limit_price=float(option_pos.current_price),
                    confidence=0.95,
                    rationale=f"Automated management rule: {management_action.value}",
                    risk_factors=["automated_management"],
                )

                self._execute_signal(signal, portfolio, state)

    def _analyze_and_trade(self, symbol: str, portfolio) -> None:
        """Analyze a symbol and potentially execute a trade."""
        state = self.wheel_manager.get_state(symbol)

        # Skip if already in an active position for this symbol
        if state.phase in (WheelPhase.CSP_OPEN, WheelPhase.CC_OPEN, WheelPhase.ROLLING):
            logger.debug(f"{symbol}: Already in phase {state.phase.value}, skipping")
            return

        # Determine what type of contracts to look at
        if state.phase == WheelPhase.IDLE:
            contract_type = "put"
        elif state.phase == WheelPhase.SHARES_HELD:
            contract_type = "call"
        else:
            return

        # 1. Get technical data
        try:
            bars = self.data_service.get_stock_bars(symbol)
            technicals = compute_technicals(symbol, bars)
        except Exception as e:
            logger.error(f"{symbol}: Technical analysis failed: {e}")
            return

        # 2. Get option chain
        try:
            contracts = self.data_service.get_option_chain(
                symbol, contract_type,
                self.settings.min_dte, self.settings.max_dte,
            )
            top_contracts = rank_contracts_for_display(
                contracts, self.settings.target_delta, top_n=5
            )
        except Exception as e:
            logger.error(f"{symbol}: Option chain fetch failed: {e}")
            return

        if not top_contracts:
            logger.info(f"{symbol}: No suitable {contract_type} contracts found")
            return

        # 3. Get news and sentiment
        news_text = ""
        sentiment_score = None
        try:
            articles = self.news_service.get_recent_news(symbol)
            news_text = self.news_service.format_news_for_llm(articles)
            sentiment_score, _ = self.sentiment.analyze_sentiment(symbol, news_text)
        except Exception as e:
            logger.warning(f"{symbol}: Sentiment analysis failed: {e}")

        # 4. Build market context
        context = MarketContext(
            symbol=symbol,
            current_price=technicals.price,
            technicals=technicals,
            top_contracts=top_contracts,
            portfolio=portfolio,
            wheel_state=state,
            news_summary=news_text[:500] if news_text else None,
            sentiment_score=sentiment_score,
        )

        # 5. Get LLM decision
        signal = self.reasoner.generate_decision(context)
        if signal is None:
            logger.warning(f"{symbol}: LLM failed to generate a signal")
            return

        # 6. If LLM says to trade, resolve the contract
        if signal.action in (TradeAction.SELL_PUT, TradeAction.SELL_CALL):
            best_contract = select_optimal_contract(
                contracts,
                target_delta=self.settings.target_delta,
                max_bid_ask_spread_pct=self.settings.max_bid_ask_spread_pct,
            )
            if best_contract:
                signal.contract_symbol = best_contract.symbol
                signal.target_strike = best_contract.strike
                signal.limit_price = round(best_contract.bid, 2) if best_contract.bid > 0 else round(best_contract.mid, 2)
                # Dynamic sizing up to available options buying power
                if signal.action == TradeAction.SELL_PUT and best_contract.strike > 0:
                    avail_power = portfolio.options_buying_power if portfolio.options_buying_power > 0 else portfolio.cash
                    cost_per_contract = best_contract.strike * 100
                    max_possible = int(avail_power / cost_per_contract)
                    if max_possible > 0:
                        signal.quantity = max(1, min(10, max_possible))
                    else:
                        logger.info(f"{symbol}: Insufficient available options buying power (${avail_power:,.2f}) for strike ${best_contract.strike:.2f}")
                        signal.action = TradeAction.HOLD
            else:
                logger.info(f"{symbol}: No optimal contract found")
                signal.action = TradeAction.HOLD

        # 7. Execute
        self._execute_signal(signal, portfolio, state, context)

    def _execute_signal(
        self,
        signal: OptionTradeSignal,
        portfolio,
        wheel_state: WheelState,
        context: Optional[MarketContext] = None,
    ) -> None:
        """Validate and execute a trade signal."""
        # Risk validation
        risk_result = self.risk_manager.validate(signal, portfolio, wheel_state)

        # Log decision
        self.journal.log_decision(
            symbol=signal.underlying,
            context=context,
            signal=signal,
            risk_result=risk_result,
            was_executed=False,
        )

        if not risk_result.passed:
            logger.info(
                f"{signal.underlying}: Signal REJECTED by risk manager: "
                f"{'; '.join(risk_result.rejections)}"
            )
            return

        if signal.action == TradeAction.HOLD:
            logger.info(f"{signal.underlying}: HOLD - no action")
            return

        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would execute: {signal.action.value} "
                f"{signal.contract_symbol} @ ${signal.limit_price}"
            )
            return

        # Execute the order
        result = self.executor.execute_signal(signal)

        if result:
            # Log the trade
            self.journal.log_trade(
                symbol=signal.contract_symbol or signal.underlying,
                underlying=signal.underlying,
                action=signal.action.value,
                side="sell" if signal.action in (TradeAction.SELL_PUT, TradeAction.SELL_CALL) else "buy",
                qty=1,
                price=signal.limit_price,
                premium=signal.limit_price * 100 if signal.action in (TradeAction.SELL_PUT, TradeAction.SELL_CALL) else None,
                order_id=result.get("order_id"),
                status=result.get("status"),
                rationale=signal.rationale,
            )

            # Update wheel state
            if signal.contract_symbol and signal.limit_price:
                self.wheel_manager.on_order_filled(
                    symbol=signal.underlying,
                    action=signal.action,
                    option_symbol=signal.contract_symbol,
                    fill_price=signal.limit_price,
                    qty=1,
                )

            # Log updated wheel state
            self.journal.log_wheel_state(
                self.wheel_manager.get_state(signal.underlying)
            )

            # Update decision as executed
            self.journal.log_decision(
                symbol=signal.underlying,
                signal=signal,
                risk_result=risk_result,
                was_executed=True,
            )

            logger.info(
                f"✅ {signal.underlying}: {signal.action.value} executed | "
                f"{signal.contract_symbol} @ ${signal.limit_price}"
            )

    def start(self) -> None:
        """Start the scheduled agent loop."""
        self._scheduler = BlockingScheduler()

        # Schedule analysis runs
        for time_str in self.settings.schedule_times:
            hour, minute = map(int, time_str.split(":"))
            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                timezone=self.settings.agent_timezone,
                day_of_week="mon-fri",
            )
            self._scheduler.add_job(
                self.run_cycle,
                trigger=trigger,
                id=f"analysis_{time_str}",
                name=f"Analysis at {time_str} ET",
                misfire_grace_time=300,
            )
            logger.info(f"Scheduled analysis at {time_str} ET (Mon-Fri)")

        # Schedule position management every 30 minutes during market hours
        mgmt_trigger = CronTrigger(
            minute="0,30",
            hour="9-16",
            timezone=self.settings.agent_timezone,
            day_of_week="mon-fri",
        )
        self._scheduler.add_job(
            self._management_check,
            trigger=mgmt_trigger,
            id="position_management",
            name="Position Management Check",
            misfire_grace_time=300,
        )

        # Handle graceful shutdown
        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received. Stopping agent...")
            self._shutdown_event.set()
            if self._scheduler:
                self._scheduler.shutdown(wait=False)

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        # Run initial cycle immediately
        logger.info("Running initial analysis cycle...")
        self.run_cycle()

        # Start scheduler
        logger.info("Agent scheduler started. Press Ctrl+C to stop.")
        try:
            self._scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Agent stopped.")
        finally:
            self.journal.close()

    def _management_check(self) -> None:
        """Periodic position management check."""
        if not self.data_service.is_market_open():
            return

        try:
            portfolio = self.data_service.get_portfolio_state()
            self.wheel_manager.sync_with_positions(portfolio)
            self._manage_existing_positions(portfolio)
        except Exception as e:
            logger.error(f"Management check failed: {e}")


def main() -> None:
    """Main entry point."""
    from alphawheel.cli.commands import app
    app()


if __name__ == "__main__":
    main()
