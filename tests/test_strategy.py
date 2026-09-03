"""Unit tests for AlphaWheel strategy components."""

import pytest
from datetime import date, datetime

from alphawheel.data.models import (
    Momentum,
    OptionContractInfo,
    OptionTradeSignal,
    PortfolioState,
    PositionInfo,
    RiskGateResult,
    TechnicalSnapshot,
    TradeAction,
    Trend,
    WheelPhase,
    WheelState,
)
from alphawheel.analysis.options_analytics import (
    compute_iv_rank,
    select_optimal_contract,
)


class TestIVRank:
    def test_compute_iv_rank_normal(self):
        iv_rank = compute_iv_rank(0.30, [0.20, 0.25, 0.30, 0.35, 0.40])
        assert iv_rank == pytest.approx(50.0)

    def test_compute_iv_rank_at_min(self):
        iv_rank = compute_iv_rank(0.20, [0.20, 0.25, 0.30, 0.35, 0.40])
        assert iv_rank == pytest.approx(0.0)

    def test_compute_iv_rank_at_max(self):
        iv_rank = compute_iv_rank(0.40, [0.20, 0.25, 0.30, 0.35, 0.40])
        assert iv_rank == pytest.approx(100.0)

    def test_compute_iv_rank_empty_history(self):
        iv_rank = compute_iv_rank(0.30, [])
        assert iv_rank == 50.0  # Default


class TestContractSelection:
    def _make_contract(self, delta: float, bid: float = 2.0, ask: float = 2.20, strike: float = 100.0) -> OptionContractInfo:
        return OptionContractInfo(
            symbol=f"TEST{int(strike)}P",
            underlying="TEST",
            contract_type="put",
            strike=strike,
            expiration=date(2025, 12, 19),
            dte=35,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2,
            delta=delta,
        )

    def test_select_closest_to_target_delta(self):
        contracts = [
            self._make_contract(delta=-0.15, strike=90),
            self._make_contract(delta=-0.25, strike=95),
            self._make_contract(delta=-0.35, strike=100),
        ]
        best = select_optimal_contract(contracts, target_delta=0.25)
        assert best is not None
        assert best.strike == 95.0

    def test_reject_wide_spread(self):
        contracts = [
            self._make_contract(delta=-0.25, bid=1.0, ask=2.0),  # 66% spread
        ]
        best = select_optimal_contract(contracts, target_delta=0.25, max_bid_ask_spread_pct=0.15)
        assert best is None

    def test_reject_no_delta(self):
        contract = OptionContractInfo(
            symbol="TESTP",
            underlying="TEST",
            contract_type="put",
            strike=100,
            expiration=date(2025, 12, 19),
            dte=35,
            bid=2.0,
            ask=2.20,
            mid=2.10,
            delta=None,
        )
        best = select_optimal_contract([contract], target_delta=0.25)
        assert best is None

    def test_empty_contracts(self):
        best = select_optimal_contract([], target_delta=0.25)
        assert best is None


class TestWheelState:
    def test_initial_state(self):
        state = WheelState(underlying="AAPL")
        assert state.phase == WheelPhase.IDLE
        assert state.shares_held == 0
        assert state.premiums_collected == 0.0

    def test_trade_signal_validation(self):
        signal = OptionTradeSignal(
            action=TradeAction.SELL_PUT,
            underlying="AAPL",
            confidence=0.85,
            rationale="Test signal",
        )
        assert signal.action == TradeAction.SELL_PUT
        assert signal.confidence == 0.85

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            OptionTradeSignal(
                action=TradeAction.SELL_PUT,
                underlying="AAPL",
                confidence=1.5,  # > 1.0 should fail
                rationale="Test",
            )


class TestRiskGateResult:
    def test_passed_result(self):
        signal = OptionTradeSignal(
            action=TradeAction.HOLD,
            underlying="AAPL",
            confidence=0.9,
            rationale="Test",
        )
        result = RiskGateResult(passed=True, signal=signal)
        assert result.passed
        assert len(result.rejections) == 0

    def test_failed_result(self):
        signal = OptionTradeSignal(
            action=TradeAction.SELL_PUT,
            underlying="AAPL",
            confidence=0.5,
            rationale="Test",
        )
        result = RiskGateResult(
            passed=False,
            signal=signal,
            rejections=["Confidence too low"],
        )
        assert not result.passed
        assert len(result.rejections) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
