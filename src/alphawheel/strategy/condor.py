"""0DTE Iron Condor strategy engine for SPY and broad-market indices."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

from alphawheel.config import AlphaWheelSettings

logger = logging.getLogger(__name__)


class CondorStatus(str, Enum):
    """Lifecycle states of an Iron Condor position."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class CondorLeg(BaseModel):
    """Represents a single leg of an Iron Condor."""
    strike: float = Field(..., description="The strike price of the option leg.")
    option_type: str = Field(..., description="'put' or 'call'")
    side: str = Field(..., description="'buy' or 'sell'")
    symbol: str = Field(..., description="OCC symbol for the option contract.")
    order_id: Optional[str] = Field(None, description="The broker order ID for this leg.")
    fill_price: Optional[float] = Field(None, description="The price at which this leg was filled.")
    bid: Optional[float] = Field(None, description="Current bid price")
    ask: Optional[float] = Field(None, description="Current ask price")


class IronCondorPosition(BaseModel):
    """Represents a complete 4-leg Iron Condor position."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID for the position")
    underlying: str = Field(..., description="The underlying symbol, e.g., SPY")
    put_long: CondorLeg = Field(..., description="Long put leg (lower strike, protection)")
    put_short: CondorLeg = Field(..., description="Short put leg (higher strike, income)")
    call_short: CondorLeg = Field(..., description="Short call leg (lower strike, income)")
    call_long: CondorLeg = Field(..., description="Long call leg (higher strike, protection)")
    quantity: int = Field(..., description="Number of spreads traded")
    entry_credit: float = Field(..., description="Total net credit received per spread upon opening")
    max_loss: float = Field(..., description="Maximum possible loss per spread (wing_width * 100 - entry_credit * 100)")
    status: CondorStatus = Field(default=CondorStatus.PENDING, description="Current status of the position")
    opened_at: datetime = Field(default_factory=datetime.utcnow, description="Time the position was opened")
    closed_at: Optional[datetime] = Field(None, description="Time the position was closed")
    close_debit: Optional[float] = Field(None, description="Net debit paid to close the spread")
    pnl: Optional[float] = Field(None, description="Realized Profit and Loss")


def _get_contract_field(contract, field_name: str):
    """Helper to extract attribute from dict or object."""
    if isinstance(contract, dict):
        return contract.get(field_name)
    return getattr(contract, field_name, None)


def _get_contract_type(contract) -> str:
    """Normalize contract type to 'put' or 'call'."""
    raw = _get_contract_field(contract, "type") or _get_contract_field(contract, "contract_type")
    if raw is None:
        return ""
    val = str(raw).lower()
    if "put" in val:
        return "put"
    if "call" in val:
        return "call"
    return val


def _get_contract_strike(contract) -> float:
    """Extract strike price as float."""
    raw = _get_contract_field(contract, "strike_price") or _get_contract_field(contract, "strike")
    return float(raw) if raw is not None else 0.0


def _get_contract_symbol(contract) -> str:
    """Extract OCC symbol."""
    return str(_get_contract_field(contract, "symbol") or "")


class IronCondorStrategy:
    """0DTE Iron Condor strategy engine.

    Manages the full lifecycle of zero days to expiration Iron Condors.
    """

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings

        self.wing_width: int = getattr(settings, "condor_wing_width", 5)
        self.target_delta: float = getattr(settings, "condor_target_delta", 0.10)
        self.profit_target_pct: float = getattr(settings, "condor_profit_target_pct", 0.50)
        self.stop_loss_pct: float = getattr(settings, "condor_stop_loss_pct", 2.0)
        self.max_capital_pct: float = getattr(settings, "condor_max_capital_pct", 0.80)
        self.close_before_minutes: int = getattr(settings, "condor_close_before_minutes", 15)

        self.positions: Dict[str, IronCondorPosition] = {}
        logger.info("Initialized IronCondorStrategy")

    def find_0dte_expiration(
        self, trading_client: TradingClient, allow_nearest: bool = True
    ) -> Optional[date]:
        """Find today's expiration date for 0DTE options on SPY.

        If allow_nearest is True and no 0DTE options exist today,
        returns the nearest future expiration.
        """
        try:
            today = datetime.now(ZoneInfo("America/New_York")).date()
            request = GetOptionContractsRequest(
                underlying_symbols=["SPY"],
                expiration_date_gte=today,
                status="active",
            )
            contracts_response = trading_client.get_option_contracts(request)
            contracts = (
                contracts_response.option_contracts
                if hasattr(contracts_response, "option_contracts")
                else contracts_response
            )

            if not contracts:
                logger.info("No active SPY option contracts found")
                return None

            available_dates = sorted(
                {
                    c.expiration_date
                    if isinstance(c.expiration_date, date)
                    else datetime.strptime(str(c.expiration_date)[:10], "%Y-%m-%d").date()
                    for c in contracts
                    if getattr(c, "expiration_date", None)
                }
            )

            if today in available_dates:
                logger.info(f"Found 0DTE options for SPY expiring today: {today}")
                return today

            if allow_nearest and available_dates:
                nearest = available_dates[0]
                logger.info(f"Today is not an expiration date; using nearest expiration: {nearest}")
                return nearest

            logger.info("No 0DTE options found for today")
            return None
        except Exception as e:
            logger.error(f"Error finding 0DTE expiration: {e}")
            return None

    def select_strikes(
        self, current_price: float, contracts: list, iv: float = 0.16
    ) -> Optional[Dict[str, float]]:
        """Select the 4 strikes for the Iron Condor based on offset percentage.

        Finds closest available strikes in the contracts list.
        """
        if not contracts:
            return None

        # Separate available strikes by type
        put_strikes = sorted(
            {_get_contract_strike(c) for c in contracts if _get_contract_type(c) == "put"}
        )
        call_strikes = sorted(
            {_get_contract_strike(c) for c in contracts if _get_contract_type(c) == "call"}
        )

        if not put_strikes or not call_strikes:
            logger.warning("Missing put or call strikes in contract list")
            return None

        # Percentage offset (~0.75% for 0DTE SPY delta ~0.10)
        offset = max(1.0, current_price * 0.0075)

        target_put_short = current_price - offset
        target_call_short = current_price + offset

        # Find closest available short strikes
        put_short = min(put_strikes, key=lambda s: abs(s - target_put_short))
        call_short = min(call_strikes, key=lambda s: abs(s - target_call_short))

        # Long strikes with wing width
        target_put_long = put_short - self.wing_width
        target_call_long = call_short + self.wing_width

        put_long = min(put_strikes, key=lambda s: abs(s - target_put_long))
        call_long = min(call_strikes, key=lambda s: abs(s - target_call_long))

        # Validation: strikes must be ordered properly
        if not (put_long < put_short <= current_price <= call_short < call_long):
            logger.warning(
                f"Selected strikes invalid order: PL={put_long}, PS={put_short}, "
                f"price={current_price}, CS={call_short}, CL={call_long}"
            )
            # Fallback adjustment
            valid_puts = [s for s in put_strikes if s < current_price]
            valid_calls = [s for s in call_strikes if s > current_price]
            if len(valid_puts) >= 2 and len(valid_calls) >= 2:
                put_short = valid_puts[-1]
                put_long = valid_puts[-2]
                call_short = valid_calls[0]
                call_long = valid_calls[1]
            else:
                return None

        strikes = {
            "put_long_strike": float(put_long),
            "put_short_strike": float(put_short),
            "call_short_strike": float(call_short),
            "call_long_strike": float(call_long),
        }

        logger.info(f"Selected strikes for Iron Condor: {strikes}")
        return strikes

    def calculate_position_size(
        self, margin_per_spread: float, available_capital: float
    ) -> int:
        """Calculate number of spreads to trade based on available capital."""
        if margin_per_spread <= 0:
            return 0

        max_spreads = int((available_capital * self.max_capital_pct) / margin_per_spread)
        quantity = max(1, min(max_spreads, 200))
        logger.info(f"Calculated position size: {quantity} spreads")
        return quantity

    def build_condor(
        self,
        underlying: str,
        strikes: Dict[str, float],
        contracts: list,
        quantity: int,
        estimated_credit: Optional[float] = None,
    ) -> Optional[IronCondorPosition]:
        """Construct an IronCondorPosition with all 4 legs."""
        matched = {}
        for contract in contracts:
            c_type = _get_contract_type(contract)
            c_strike = _get_contract_strike(contract)
            c_symbol = _get_contract_symbol(contract)

            if c_type == "put" and abs(c_strike - strikes["put_long_strike"]) < 0.01:
                matched["put_long"] = (c_symbol, contract)
            elif c_type == "put" and abs(c_strike - strikes["put_short_strike"]) < 0.01:
                matched["put_short"] = (c_symbol, contract)
            elif c_type == "call" and abs(c_strike - strikes["call_short_strike"]) < 0.01:
                matched["call_short"] = (c_symbol, contract)
            elif c_type == "call" and abs(c_strike - strikes["call_long_strike"]) < 0.01:
                matched["call_long"] = (c_symbol, contract)

        if len(matched) < 4:
            logger.error(f"Could not find matching symbols for all 4 legs. Found: {list(matched.keys())}")
            return None

        # Build legs
        put_long_leg = CondorLeg(
            strike=strikes["put_long_strike"],
            option_type="put",
            side="buy",
            symbol=matched["put_long"][0],
        )
        put_short_leg = CondorLeg(
            strike=strikes["put_short_strike"],
            option_type="put",
            side="sell",
            symbol=matched["put_short"][0],
        )
        call_short_leg = CondorLeg(
            strike=strikes["call_short_strike"],
            option_type="call",
            side="sell",
            symbol=matched["call_short"][0],
        )
        call_long_leg = CondorLeg(
            strike=strikes["call_long_strike"],
            option_type="call",
            side="buy",
            symbol=matched["call_long"][0],
        )

        credit = estimated_credit if estimated_credit and estimated_credit > 0 else 1.0
        wing = strikes["put_short_strike"] - strikes["put_long_strike"]
        max_loss = (wing * 100) - (credit * 100)

        position = IronCondorPosition(
            underlying=underlying,
            put_long=put_long_leg,
            put_short=put_short_leg,
            call_short=call_short_leg,
            call_long=call_long_leg,
            quantity=quantity,
            entry_credit=credit,
            max_loss=max_loss,
            status=CondorStatus.PENDING,
        )

        self.positions[position.id] = position
        logger.info(f"Built Iron Condor position {position.id[:8]} ({quantity} spreads @ ${credit:.2f} credit)")
        return position

    def should_close(
        self, position: IronCondorPosition, current_spread_cost: float
    ) -> Tuple[bool, str]:
        """Evaluate if the position should be closed.

        Args:
            position: The open IronCondorPosition.
            current_spread_cost: Net debit to close per spread.
        """
        if position.status not in (CondorStatus.OPEN, CondorStatus.PENDING):
            return False, "Not open"

        # 1. Profit target check (50% max profit)
        profit_target_cost = position.entry_credit * (1.0 - self.profit_target_pct)
        if current_spread_cost <= profit_target_cost:
            return (
                True,
                f"Profit target reached: close cost ${current_spread_cost:.2f} <= target ${profit_target_cost:.2f}",
            )

        # 2. Stop loss check
        stop_loss_cost = position.entry_credit * (1.0 + self.stop_loss_pct)
        if current_spread_cost >= stop_loss_cost:
            return (
                True,
                f"Stop loss triggered: close cost ${current_spread_cost:.2f} >= stop ${stop_loss_cost:.2f}",
            )

        # 3. Time check (close before 4:00 PM ET)
        now_et = datetime.now(ZoneInfo("America/New_York"))
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        minutes_to_close = (market_close - now_et).total_seconds() / 60.0

        if 0 < minutes_to_close <= self.close_before_minutes:
            return True, f"EOD close: {minutes_to_close:.1f} min to market close"

        return False, ""

    def close_position(self, position_id: str, close_debit: float) -> None:
        """Update the position state after closing."""
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found")
            return

        pos = self.positions[position_id]
        pos.status = CondorStatus.CLOSED
        pos.closed_at = datetime.utcnow()
        pos.close_debit = close_debit
        pos.pnl = (pos.entry_credit - close_debit) * 100 * pos.quantity
        logger.info(f"Closed position {position_id[:8]} with PnL: ${pos.pnl:+.2f}")

    def get_open_positions(self) -> List[IronCondorPosition]:
        """Return all open positions."""
        return [p for p in self.positions.values() if p.status == CondorStatus.OPEN]

    def get_summary(self) -> Dict:
        """Return summary stats."""
        total = len(self.positions)
        open_count = len(self.get_open_positions())
        total_pnl = sum(p.pnl for p in self.positions.values() if p.pnl is not None)
        return {
            "total_positions": total,
            "open_positions": open_count,
            "total_pnl": total_pnl,
        }
