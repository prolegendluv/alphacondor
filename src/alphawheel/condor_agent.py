"""0DTE Iron Condor agent orchestrator.

Coordinates the condor strategy: finds 0DTE options, selects strikes,
sizes positions, executes multi-leg orders, and manages throughout the day.
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, date
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.market_data import MarketDataService
from alphawheel.journal.trade_log import TradeJournal

logger = logging.getLogger(__name__)


class CondorAgent:
    """Orchestrates 0DTE Iron Condor trading on SPY.

    Daily workflow:
    1. At market open (~9:45 ET): scan for 0DTE options, open condors
    2. Every 15 min: check profit targets, stop losses, and time-based exits
    3. At 3:45 ET: close any remaining positions
    """

    def __init__(
        self,
        settings: AlphaWheelSettings,
        dry_run: bool = False,
        symbol: str = "SPY",
    ):
        self.settings = settings
        self.dry_run = dry_run
        self.symbol = symbol

        # Components
        self.data_service = MarketDataService(settings)
        self.journal = TradeJournal(settings.db_path)

        # Lazy imports to avoid circular deps
        self._strategy = None
        self._executor = None

        self._scheduler: Optional[BlockingScheduler] = None
        self._shutdown_event = threading.Event()
        self._today_opened = False  # Track if we've opened today

        logger.info(
            f"CondorAgent initialized | Symbol: {symbol} | "
            f"Dry Run: {dry_run} | Paper: {settings.alpaca_paper}"
        )

    @property
    def strategy(self):
        if self._strategy is None:
            from alphawheel.strategy.condor import IronCondorStrategy
            self._strategy = IronCondorStrategy(self.settings)
        return self._strategy

    @property
    def executor(self):
        if self._executor is None:
            from alphawheel.execution.condor_executor import CondorExecutor
            self._executor = CondorExecutor(self.settings)
        return self._executor

    def run_open_cycle(self, force: bool = False) -> None:
        """Open new 0DTE Iron Condor positions."""
        logger.info("=" * 60)
        logger.info(f"CONDOR OPEN CYCLE | {datetime.now()}")
        logger.info("=" * 60)

        # Check market
        if not force and not self.data_service.is_market_open():
            logger.info("Market is closed. Skipping. (Use --force to override)")
            return

        # Don't open twice in one day
        if self._today_opened:
            logger.info("Already opened condors today. Skipping open cycle.")
            return

        # Get portfolio state
        try:
            portfolio = self.data_service.get_portfolio_state()
            self.journal.log_portfolio_snapshot(portfolio)
            logger.info(
                f"Portfolio | Equity: ${portfolio.equity:,.2f} | "
                f"Cash: ${portfolio.cash:,.2f} | "
                f"Buying Power: ${portfolio.buying_power:,.2f}"
            )
        except Exception as e:
            logger.error(f"Failed to get portfolio: {e}")
            return

        # Get current price
        try:
            current_price = self.data_service.get_stock_price(self.symbol)
            logger.info(f"{self.symbol} price: ${current_price:.2f}")
        except Exception as e:
            logger.error(f"Failed to get {self.symbol} price: {e}")
            return

        # Find 0DTE expiration
        from alpaca.trading.client import TradingClient
        trading_client = TradingClient(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
            paper=self.settings.alpaca_paper,
        )

        exp_date = self.strategy.find_0dte_expiration(trading_client, allow_nearest=True)
        if not exp_date:
            logger.warning("No 0DTE or near-term options available")
            return

        logger.info(f"0DTE expiration: {exp_date}")

        # Get option contracts for that expiration
        from alpaca.trading.requests import GetOptionContractsRequest
        atm = round(current_price)
        wing = getattr(self.strategy, 'wing_width', 5)

        req = GetOptionContractsRequest(
            underlying_symbols=[self.symbol],
            expiration_date=str(exp_date),
            strike_price_gte=str(atm - wing - 15),
            strike_price_lte=str(atm + wing + 15),
            status="active",
        )
        result = trading_client.get_option_contracts(req)
        contracts = result.option_contracts if hasattr(result, 'option_contracts') else result

        if not contracts:
            logger.warning("No option contracts found for today's expiry")
            return

        logger.info(f"Found {len(contracts)} contracts for {exp_date}")

        # Select strikes
        # Convert contracts to a list of dicts for the strategy
        contract_list = []
        for c in contracts:
            contract_list.append({
                "symbol": c.symbol,
                "strike": float(c.strike_price),
                "type": str(c.type).lower().replace("contracttype.", ""),
                "expiration": str(c.expiration_date),
            })

        strikes = self.strategy.select_strikes(current_price, contract_list)
        if not strikes:
            logger.warning("Could not find suitable strikes")
            return

        logger.info(
            f"Selected strikes: "
            f"Put spread: ${strikes['put_long_strike']}/{strikes['put_short_strike']} | "
            f"Call spread: ${strikes['call_short_strike']}/{strikes['call_long_strike']}"
        )

        # Calculate position size
        margin_per = wing * 100
        available = portfolio.buying_power
        num_spreads = self.strategy.calculate_position_size(margin_per, available)
        logger.info(f"Position size: {num_spreads} spreads (margin: ${margin_per * num_spreads:,.0f})")

        # Build condor position
        position = self.strategy.build_condor(
            underlying=self.symbol,
            strikes=strikes,
            contracts=contract_list,
            quantity=num_spreads,
        )

        if not position:
            logger.error(f"Failed to build condor position for strikes {strikes}")
            return

        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would open {num_spreads}x Iron Condor on {self.symbol}:\n"
                f"  Buy  Put  @ ${position.put_long.strike} ({position.put_long.symbol})\n"
                f"  Sell Put  @ ${position.put_short.strike} ({position.put_short.symbol})\n"
                f"  Sell Call @ ${position.call_short.strike} ({position.call_short.symbol})\n"
                f"  Buy  Call @ ${position.call_long.strike} ({position.call_long.symbol})\n"
                f"  Est. Credit: ${position.entry_credit:.2f}/spread\n"
                f"  Max Loss: ${position.max_loss:.2f}/spread\n"
                f"  Total margin: ${margin_per * num_spreads:,.0f}"
            )
            self._today_opened = True
            return

        # Execute
        logger.info(f"Opening {num_spreads}x Iron Condor...")
        result = self.executor.open_condor(position)

        if result.get("success"):
            self.strategy.positions[position.id] = position
            self._today_opened = True

            # Log to journal
            self.journal.log_trade(
                symbol=f"CONDOR-{self.symbol}",
                underlying=self.symbol,
                action="OPEN_CONDOR",
                side="sell",
                qty=num_spreads,
                price=position.entry_credit,
                premium=position.entry_credit * 100 * num_spreads,
                order_id=str(result.get("order_ids", {}))[: 100],
                status="FILLED",
                rationale=(
                    f"0DTE Iron Condor: "
                    f"P:{position.put_long.strike}/{position.put_short.strike} "
                    f"C:{position.call_short.strike}/{position.call_long.strike}"
                ),
            )

            # Log decision
            self.journal.log_decision(
                symbol=self.symbol,
                context=None,
                signal=None,
                risk_result=None,
                was_executed=True,
            )

            logger.info(
                f"Iron Condor OPENED | {num_spreads} spreads | "
                f"Credit: ${position.entry_credit:.2f}/spread | "
                f"Total credit: ${position.entry_credit * 100 * num_spreads:,.2f}"
            )
        else:
            logger.error(f"Failed to open condor: {result}")

    def run_management_cycle(self) -> None:
        """Check open condor positions for profit targets, stop losses, and time exits."""
        if not self.data_service.is_market_open():
            return

        open_positions = self.strategy.get_open_positions()
        if not open_positions:
            return

        logger.info(f"Managing {len(open_positions)} open condor position(s)...")

        for position in open_positions:
            try:
                # Get current market value of the condor
                current_value = self.executor.get_condor_market_value(position)
                if current_value is None:
                    logger.warning(f"Could not get market value for condor {position.id[:8]}")
                    continue

                # Check if we should close
                should_close, reason = self.strategy.should_close(position, current_value)

                if should_close:
                    logger.info(f"Closing condor {position.id[:8]}: {reason}")

                    if self.dry_run:
                        logger.info(f"[DRY RUN] Would close condor: {reason}")
                        continue

                    result = self.executor.close_condor(position)
                    if result.get("success"):
                        pnl = (position.entry_credit - current_value) * 100 * position.quantity
                        self.strategy.close_position(position.id, current_value)

                        self.journal.log_trade(
                            symbol=f"CONDOR-{self.symbol}",
                            underlying=self.symbol,
                            action="CLOSE_CONDOR",
                            side="buy",
                            qty=position.quantity,
                            price=current_value,
                            premium=pnl,
                            order_id=str(result.get("order_ids", {}))[:100],
                            status="FILLED",
                            rationale=reason,
                        )

                        logger.info(
                            f"Condor CLOSED | P&L: ${pnl:+,.2f} | Reason: {reason}"
                        )
                    else:
                        logger.error(f"Failed to close condor: {result}")
                else:
                    # Log current status
                    pnl = (position.entry_credit - current_value) * 100 * position.quantity
                    logger.info(
                        f"Condor {position.id[:8]} | "
                        f"Entry: ${position.entry_credit:.2f} | "
                        f"Current: ${current_value:.2f} | "
                        f"P&L: ${pnl:+,.2f}"
                    )

            except Exception as e:
                logger.error(f"Error managing condor {position.id[:8]}: {e}", exc_info=True)

    def run_once(self, force: bool = False) -> None:
        """Run a single open + manage cycle."""
        self.run_open_cycle(force=force)
        self.run_management_cycle()

    def start(self) -> None:
        """Start the scheduled condor agent loop."""
        self._scheduler = BlockingScheduler()

        # Open condors at 9:45 AM ET (15 min after open for prices to settle)
        open_trigger = CronTrigger(
            hour=9, minute=45,
            timezone=self.settings.agent_timezone,
            day_of_week="mon-fri",
        )
        self._scheduler.add_job(
            self.run_open_cycle,
            trigger=open_trigger,
            id="condor_open",
            name="Open 0DTE Iron Condors (9:45 ET)",
            misfire_grace_time=300,
        )

        # Check positions every 15 minutes
        mgmt_trigger = CronTrigger(
            minute="0,15,30,45",
            hour="10-15",
            timezone=self.settings.agent_timezone,
            day_of_week="mon-fri",
        )
        self._scheduler.add_job(
            self.run_management_cycle,
            trigger=mgmt_trigger,
            id="condor_manage",
            name="Manage Condor Positions (every 15 min)",
            misfire_grace_time=300,
        )

        # Force close any remaining at 3:45 PM ET
        close_trigger = CronTrigger(
            hour=15, minute=45,
            timezone=self.settings.agent_timezone,
            day_of_week="mon-fri",
        )
        self._scheduler.add_job(
            self._force_close_all,
            trigger=close_trigger,
            id="condor_eod_close",
            name="Force Close All Condors (3:45 ET)",
            misfire_grace_time=300,
        )

        # Reset daily state at 9:30 AM ET
        reset_trigger = CronTrigger(
            hour=9, minute=30,
            timezone=self.settings.agent_timezone,
            day_of_week="mon-fri",
        )
        self._scheduler.add_job(
            self._daily_reset,
            trigger=reset_trigger,
            id="condor_daily_reset",
            name="Daily Reset (9:30 ET)",
            misfire_grace_time=300,
        )

        # Handle graceful shutdown
        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received. Stopping condor agent...")
            self._shutdown_event.set()
            if self._scheduler:
                self._scheduler.shutdown(wait=False)

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        # Run initial cycle
        logger.info("Running initial condor cycle...")
        self.run_once(force=True)

        # Start scheduler
        logger.info(
            "Condor agent scheduler started.\n"
            "  Open:   9:45 AM ET\n"
            "  Manage: Every 15 min (10 AM - 4 PM ET)\n"
            "  Close:  3:45 PM ET\n"
            "Press Ctrl+C to stop."
        )
        try:
            self._scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Condor agent stopped.")
        finally:
            self.journal.close()

    def _force_close_all(self) -> None:
        """Force close all open condor positions at end of day."""
        open_positions = self.strategy.get_open_positions()
        if not open_positions:
            logger.info("No open condors to close at EOD.")
            return

        logger.info(f"EOD: Force closing {len(open_positions)} condor(s)...")
        for position in open_positions:
            try:
                if self.dry_run:
                    logger.info(f"[DRY RUN] Would force close condor {position.id[:8]}")
                    continue

                result = self.executor.close_condor(position)
                if result.get("success"):
                    current_value = self.executor.get_condor_market_value(position) or 0.0
                    pnl = (position.entry_credit - current_value) * 100 * position.quantity
                    self.strategy.close_position(position.id, current_value)

                    self.journal.log_trade(
                        symbol=f"CONDOR-{self.symbol}",
                        underlying=self.symbol,
                        action="CLOSE_CONDOR",
                        side="buy",
                        qty=position.quantity,
                        price=current_value,
                        premium=pnl,
                        status="FILLED",
                        rationale="End-of-day forced close",
                    )
                    logger.info(f"EOD close | P&L: ${pnl:+,.2f}")
            except Exception as e:
                logger.error(f"EOD close failed for {position.id[:8]}: {e}")

    def _daily_reset(self) -> None:
        """Reset daily state for new trading day."""
        self._today_opened = False
        logger.info("Daily reset: ready for new condor trades.")
