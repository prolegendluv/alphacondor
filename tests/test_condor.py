"""Unit tests for the 0DTE Iron Condor strategy."""

import pytest
from datetime import date, datetime
from alphawheel.config import AlphaWheelSettings
from alphawheel.strategy.condor import (
    IronCondorStrategy,
    CondorStatus,
    CondorLeg,
    IronCondorPosition,
)


@pytest.fixture
def dummy_settings():
    return AlphaWheelSettings(
        alpaca_api_key="test_key",
        alpaca_secret_key="test_secret",
        condor_wing_width=5,
        condor_target_delta=0.10,
        condor_profit_target_pct=0.50,
        condor_stop_loss_pct=2.0,
        condor_max_capital_pct=0.80,
        condor_close_before_minutes=15,
    )


@pytest.fixture
def mock_contracts():
    """Generates a realistic set of SPY contracts around $765."""
    contracts = []
    # Puts from 740 to 770
    for strike in range(740, 775, 1):
        contracts.append({
            "symbol": f"SPY260908P00{strike}000",
            "strike_price": float(strike),
            "type": "put",
            "expiration_date": "2026-09-08",
        })
    # Calls from 760 to 790
    for strike in range(760, 795, 1):
        contracts.append({
            "symbol": f"SPY260908C00{strike}000",
            "strike_price": float(strike),
            "type": "call",
            "expiration_date": "2026-09-08",
        })
    return contracts


class TestIronCondorStrategy:
    def test_strike_selection(self, dummy_settings, mock_contracts):
        strategy = IronCondorStrategy(dummy_settings)
        current_price = 765.0

        strikes = strategy.select_strikes(current_price, mock_contracts)
        assert strikes is not None

        # Verify proper strike order: put_long < put_short <= price <= call_short < call_long
        pl = strikes["put_long_strike"]
        ps = strikes["put_short_strike"]
        cs = strikes["call_short_strike"]
        cl = strikes["call_long_strike"]

        assert pl < ps <= current_price <= cs < cl
        assert ps - pl == dummy_settings.condor_wing_width
        assert cl - cs == dummy_settings.condor_wing_width

    def test_position_sizing(self, dummy_settings):
        strategy = IronCondorStrategy(dummy_settings)
        margin_per_spread = 500.0  # $5 wing * 100
        available_capital = 100_000.0

        qty = strategy.calculate_position_size(margin_per_spread, available_capital)
        # 100,000 * 0.80 / 500 = 160
        assert qty == 160

    def test_build_condor(self, dummy_settings, mock_contracts):
        strategy = IronCondorStrategy(dummy_settings)
        strikes = {
            "put_long_strike": 755.0,
            "put_short_strike": 760.0,
            "call_short_strike": 770.0,
            "call_long_strike": 775.0,
        }

        pos = strategy.build_condor("SPY", strikes, mock_contracts, quantity=10, estimated_credit=1.20)
        assert pos is not None
        assert pos.quantity == 10
        assert pos.entry_credit == 1.20
        assert pos.max_loss == (5.0 * 100) - (1.20 * 100)  # 500 - 120 = 380
        assert pos.put_short.strike == 760.0
        assert pos.put_long.strike == 755.0
        assert pos.call_short.strike == 770.0
        assert pos.call_long.strike == 775.0
        assert pos.put_short.symbol == "SPY260908P00760000"
        assert pos.call_short.symbol == "SPY260908C00770000"

    def test_profit_target_exit(self, dummy_settings, mock_contracts):
        strategy = IronCondorStrategy(dummy_settings)
        strikes = {
            "put_long_strike": 755.0,
            "put_short_strike": 760.0,
            "call_short_strike": 770.0,
            "call_long_strike": 775.0,
        }
        pos = strategy.build_condor("SPY", strikes, mock_contracts, quantity=1, estimated_credit=1.00)
        pos.status = CondorStatus.OPEN

        # Profit target is 50% profit => cost to close <= $0.50
        # If current cost to close is $0.40 -> should close
        should_close, reason = strategy.should_close(pos, current_spread_cost=0.40)
        assert should_close is True
        assert "Profit target reached" in reason

        # If current cost to close is $0.80 -> should not close
        should_close, reason = strategy.should_close(pos, current_spread_cost=0.80)
        assert should_close is False

    def test_stop_loss_exit(self, dummy_settings, mock_contracts):
        strategy = IronCondorStrategy(dummy_settings)
        strikes = {
            "put_long_strike": 755.0,
            "put_short_strike": 760.0,
            "call_short_strike": 770.0,
            "call_long_strike": 775.0,
        }
        pos = strategy.build_condor("SPY", strikes, mock_contracts, quantity=1, estimated_credit=1.00)
        pos.status = CondorStatus.OPEN

        # Stop loss is 200% loss => cost to close >= $1.00 * (1 + 2.0) = $3.00
        should_close, reason = strategy.should_close(pos, current_spread_cost=3.20)
        assert should_close is True
        assert "Stop loss triggered" in reason

    def test_close_and_pnl(self, dummy_settings, mock_contracts):
        strategy = IronCondorStrategy(dummy_settings)
        strikes = {
            "put_long_strike": 755.0,
            "put_short_strike": 760.0,
            "call_short_strike": 770.0,
            "call_long_strike": 775.0,
        }
        pos = strategy.build_condor("SPY", strikes, mock_contracts, quantity=10, estimated_credit=1.00)
        pos.status = CondorStatus.OPEN

        # Close with debit of $0.40 per spread (profit of $0.60 per share = $60 per spread * 10 spreads = $600)
        strategy.close_position(pos.id, close_debit=0.40)
        assert pos.status == CondorStatus.CLOSED
        assert pos.pnl == 600.0

        summary = strategy.get_summary()
        assert summary["total_positions"] == 1
        assert summary["open_positions"] == 0
        assert summary["total_pnl"] == 600.0
