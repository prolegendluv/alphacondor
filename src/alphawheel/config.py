"""AlphaWheel configuration and environment variable management."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class AlphaWheelSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Alpaca API
    alpaca_api_key: str = Field(
        default_factory=lambda: os.getenv("ALPACA_API_KEY", "PKUORT3YYHNZ4HXWT34KIN2B52"),
        description="Alpaca API Key ID",
    )
    alpaca_secret_key: str = Field(
        default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", "GVkwxEBRUFVnH543S7h4S3q4YxxdtZG1WfSbX1bpRhGR"),
        description="Alpaca API Secret Key",
    )
    alpaca_paper: bool = Field(True, description="Use paper trading environment")

    # LLM
    google_api_key: str = Field("", description="Google Gemini API key")
    llm_model: str = Field("gemini-3.5-flash-lite", description="LLM model name")

    # Agent Schedule
    agent_schedule_times: str = Field("10:00,15:00", description="Comma-separated HH:MM times (ET)")
    agent_timezone: str = Field("US/Eastern", description="Timezone for scheduling")
    log_level: str = Field("INFO", description="Logging level")

    # Strategy Parameters
    universe: list[str] = Field(
        default=["SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "TSLA", "UBER", "SOFI", "INTC", "PFE"],
        description="Universe of underlying symbols to trade",
    )
    target_delta: float = Field(0.30, ge=0.10, le=0.50, description="Target option delta")
    min_dte: int = Field(30, ge=7, description="Minimum days to expiration")
    max_dte: int = Field(45, le=90, description="Maximum days to expiration")
    min_iv_rank: float = Field(25.0, ge=0, le=100, description="Minimum IV Rank to enter")
    profit_target_pct: float = Field(0.50, description="Close at this % of max profit")
    management_dte: int = Field(21, description="DTE threshold to roll/close")
    max_loss_multiplier: float = Field(2.0, description="Stop loss at N× premium received")
    max_ticker_allocation: float = Field(1.0, description="Max portfolio % per underlying")
    max_total_delta: float = Field(1.0, description="Max aggregate portfolio delta")
    max_concurrent_positions: int = Field(20, description="Max number of concurrent wheel positions")
    min_confidence: float = Field(0.70, description="Minimum LLM confidence to trade")
    max_bid_ask_spread_pct: float = Field(0.15, description="Max bid-ask spread as % of mid")
    risk_free_rate: float = Field(0.045, description="Risk-free rate for BS model")
    
    # Iron Condor Parameters
    condor_wing_width: int = Field(5, description="Wing width in dollars for condors")
    condor_target_delta: float = Field(0.10, description="Target delta for condor short strikes")
    condor_profit_target_pct: float = Field(0.50, description="Condor profit take threshold")
    condor_stop_loss_pct: float = Field(2.0, description="Condor stop loss multiplier")
    condor_max_capital_pct: float = Field(0.80, description="Max capital percentage for condors")
    condor_close_before_minutes: int = Field(15, description="Minutes before market close to exit 0DTE condors")

    # Paths
    db_path: Path = Field(Path("data/alphawheel.db"), description="SQLite database path")

    @field_validator("universe", mode="before")
    @classmethod
    def parse_universe(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        return v

    @property
    def schedule_times(self) -> list[str]:
        return [t.strip() for t in self.agent_schedule_times.split(",")]


def get_settings() -> AlphaWheelSettings:
    """Load and return application settings."""
    # Check streamlit secrets if running inside Streamlit Cloud
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GOOGLE_API_KEY", "ALPACA_PAPER"]:
                if k in st.secrets:
                    os.environ.setdefault(k, str(st.secrets[k]))
                elif k.lower() in st.secrets:
                    os.environ.setdefault(k, str(st.secrets[k.lower()]))
    except Exception:
        pass

    # Try loading .env from project root
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        os.environ.setdefault("ENV_FILE", str(env_path))
    return AlphaWheelSettings(_env_file=env_path if env_path.exists() else None)
