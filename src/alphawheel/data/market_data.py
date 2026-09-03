"""Market data service for fetching stock and options data from Alpaca."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from alphawheel.config import AlphaWheelSettings
from alphawheel.data.models import OptionContractInfo, PortfolioState, PositionInfo

logger = logging.getLogger(__name__)


class MarketDataService:
    """Fetches stock and options market data from Alpaca APIs."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self.trading_client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
        self.stock_client = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )
        self.option_client = OptionHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )

    def get_stock_price(self, symbol: str) -> float:
        """Get the latest stock price for a symbol."""
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.stock_client.get_stock_latest_quote(req)
            quote = quotes[symbol]
            # Use midpoint of bid/ask, fallback to either
            bid = float(quote.bid_price or 0)
            ask = float(quote.ask_price or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            return ask if ask > 0 else bid
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            raise

    def get_stock_bars(self, symbol: str, days: int = 200) -> pd.DataFrame:
        """Get historical daily bars for technical analysis."""
        start = datetime.now() - timedelta(days=days + 10)  # buffer for weekends
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
        )
        bars = self.stock_client.get_stock_bars(req)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.loc[symbol]
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        return df.tail(days)

    def get_option_chain(
        self,
        symbol: str,
        contract_type: str = "put",
        min_dte: int = 30,
        max_dte: int = 45,
    ) -> list[OptionContractInfo]:
        """Fetch option chain with Greeks for a given underlying."""
        today = date.today()
        exp_gte = today + timedelta(days=min_dte)
        exp_lte = today + timedelta(days=max_dte)

        # First get available contracts
        ct = ContractType.PUT if contract_type == "put" else ContractType.CALL
        contracts_req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status="active",
            type=ct,
            expiration_date_gte=exp_gte,
            expiration_date_lte=exp_lte,
            limit=100,
        )

        try:
            contracts_resp = self.trading_client.get_option_contracts(contracts_req)
            contracts = contracts_resp.option_contracts or []
        except Exception as e:
            logger.error(f"Failed to get option contracts for {symbol}: {e}")
            return []

        if not contracts:
            logger.info(f"No {contract_type} contracts found for {symbol} ({min_dte}-{max_dte} DTE)")
            return []

        # Fetch chain snapshots with Greeks
        try:
            chain_req = OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date_gte=exp_gte,
                expiration_date_lte=exp_lte,
            )
            snapshots = self.option_client.get_option_chain(chain_req)
        except Exception as e:
            logger.warning(f"Failed to get option chain snapshots for {symbol}: {e}")
            snapshots = {}

        result = []
        stock_price = self.get_stock_price(symbol)

        for contract in contracts:
            snapshot = snapshots.get(contract.symbol)
            bid, ask = 0.0, 0.0
            iv, delta, gamma, theta, vega, rho = None, None, None, None, None, None

            if snapshot and snapshot.latest_quote:
                bid = float(snapshot.latest_quote.bid_price or 0)
                ask = float(snapshot.latest_quote.ask_price or 0)
            if snapshot and hasattr(snapshot, "implied_volatility"):
                iv = snapshot.implied_volatility
            if snapshot and snapshot.greeks:
                delta = snapshot.greeks.delta
                gamma = snapshot.greeks.gamma
                theta = snapshot.greeks.theta
                vega = snapshot.greeks.vega
                rho = snapshot.greeks.rho

            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            if mid < 0.05:  # Skip illiquid/worthless
                continue

            dte = (contract.expiration_date - today).days

            result.append(
                OptionContractInfo(
                    symbol=contract.symbol,
                    underlying=symbol,
                    contract_type=contract_type,
                    strike=float(contract.strike_price),
                    expiration=contract.expiration_date,
                    dte=dte,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    implied_volatility=iv,
                    delta=delta,
                    gamma=gamma,
                    theta=theta,
                    vega=vega,
                    rho=rho,
                )
            )

        logger.info(f"Found {len(result)} valid {contract_type} contracts for {symbol}")
        return sorted(result, key=lambda c: abs(c.strike - stock_price))

    def get_portfolio_state(self) -> PortfolioState:
        """Fetch current account and position state."""
        account = self.trading_client.get_account()
        raw_positions = self.trading_client.get_all_positions()

        positions = []
        for pos in raw_positions:
            positions.append(
                PositionInfo(
                    symbol=pos.symbol,
                    qty=int(pos.qty),
                    avg_entry_price=float(pos.avg_entry_price),
                    current_price=float(pos.current_price),
                    unrealized_pl=float(pos.unrealized_pl),
                    unrealized_plpc=float(pos.unrealized_plpc or 0),
                    asset_class=str(pos.asset_class),
                    side="long" if int(pos.qty) > 0 else "short",
                )
            )

        return PortfolioState(
            equity=float(account.equity),
            cash=float(account.cash),
            buying_power=float(account.buying_power),
            options_buying_power=float(getattr(account, "options_buying_power", None) or account.cash),
            positions=positions,
            total_pl=float(account.equity) - 100000.0,  # Assume 100k starting
        )

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        clock = self.trading_client.get_clock()
        return clock.is_open
