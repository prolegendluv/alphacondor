"""SQLite-based trade journal for audit trail and performance tracking.

Records all trades, agent decisions, and portfolio snapshots
for hackathon judging and debugging.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from alphawheel.data.models import (
    MarketContext,
    OptionTradeSignal,
    PortfolioState,
    RiskGateResult,
    WheelState,
)

logger = logging.getLogger(__name__)


class TradeJournal:
    """Persistent trade journal using SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Initialize database tables."""
        cursor = self.conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                underlying TEXT NOT NULL,
                action TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL,
                premium REAL,
                order_id TEXT,
                status TEXT,
                rationale TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wheel_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                underlying TEXT NOT NULL,
                phase TEXT NOT NULL,
                cost_basis REAL,
                premiums_collected REAL,
                shares_held INTEGER,
                current_option TEXT,
                state_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                cash REAL NOT NULL,
                buying_power REAL,
                total_pl REAL,
                positions_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS agent_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market_context_json TEXT,
                llm_response_json TEXT,
                risk_result_json TEXT,
                action_taken TEXT,
                was_executed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_underlying ON trades(underlying);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON portfolio_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON agent_decisions(symbol);
        """)

        self.conn.commit()
        logger.info(f"Trade journal initialized at {self.db_path}")

    def log_trade(
        self,
        symbol: str,
        underlying: str,
        action: str,
        side: str,
        qty: int,
        price: Optional[float] = None,
        premium: Optional[float] = None,
        order_id: Optional[str] = None,
        status: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> int:
        """Log a trade execution."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (timestamp, symbol, underlying, action, side, qty, price, premium, order_id, status, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                symbol,
                underlying,
                action,
                side,
                qty,
                price,
                premium,
                order_id,
                status,
                rationale,
            ),
        )
        self.conn.commit()
        trade_id = cursor.lastrowid
        logger.info(f"Trade logged: #{trade_id} {action} {symbol} @ ${price}")
        return trade_id

    def log_wheel_state(self, state: WheelState) -> None:
        """Log a wheel state snapshot."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO wheel_states (timestamp, underlying, phase, cost_basis, premiums_collected, shares_held, current_option, state_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                state.underlying,
                state.phase.value,
                state.cost_basis,
                state.premiums_collected,
                state.shares_held,
                state.current_option_symbol,
                state.model_dump_json(),
            ),
        )
        self.conn.commit()

    def log_portfolio_snapshot(self, portfolio: PortfolioState) -> None:
        """Log a portfolio snapshot."""
        positions_json = json.dumps(
            [p.model_dump() for p in portfolio.positions],
            default=str,
        )
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO portfolio_snapshots (timestamp, equity, cash, buying_power, total_pl, positions_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                portfolio.equity,
                portfolio.cash,
                portfolio.buying_power,
                portfolio.total_pl,
                positions_json,
            ),
        )
        self.conn.commit()

    def log_decision(
        self,
        symbol: str,
        context: Optional[MarketContext] = None,
        signal: Optional[OptionTradeSignal] = None,
        risk_result: Optional[RiskGateResult] = None,
        was_executed: bool = False,
    ) -> None:
        """Log an agent decision with full context."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_decisions (timestamp, symbol, market_context_json, llm_response_json, risk_result_json, action_taken, was_executed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                symbol,
                context.model_dump_json() if context else None,
                signal.model_dump_json() if signal else None,
                risk_result.model_dump_json() if risk_result else None,
                signal.action.value if signal else None,
                1 if was_executed else 0,
            ),
        )
        self.conn.commit()

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Get recent trades."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_portfolio_history(self, limit: int = 100) -> list[dict]:
        """Get portfolio equity history."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT timestamp, equity, cash, total_pl FROM portfolio_snapshots ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_total_premiums(self) -> float:
        """Get total premiums collected across all trades."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(premium), 0) as total FROM trades WHERE action IN ('SELL_PUT', 'SELL_CALL')"
        )
        return float(cursor.fetchone()["total"])

    def get_decision_history(self, symbol: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Get agent decision history."""
        cursor = self.conn.cursor()
        if symbol:
            cursor.execute(
                "SELECT * FROM agent_decisions WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
