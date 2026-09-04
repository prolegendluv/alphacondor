# ⚙️ AlphaWheel — Autonomous AI Wheel Strategy Trading Agent

> Built for the [lablab.ai x Alpaca Hackathon](https://lablab.ai) — Track 04: Income & Portfolio Overlay Agents

AlphaWheel is an autonomous AI trading agent that executes the **Wheel Strategy** (Cash-Secured Puts ↔ Covered Calls) on Alpaca's paper trading platform. It combines **LLM-powered market reasoning** (Google Gemini) with **deterministic risk guardrails** and **technical analysis** to generate consistent options income.

## ✨ Features

- **Autonomous Wheel Strategy**: Fully automated CSP → Assignment → Covered Call → Called Away cycle
- **LLM-Powered Decisions**: Google Gemini analyzes technicals, IV, sentiment, and portfolio state
- **Deterministic Risk Gates**: Buying power, concentration limits, delta caps, earnings guards
- **Real-Time Monitoring**: WebSocket-based fill tracking and position management
- **Smart Position Management**: 50% profit target, 21 DTE roll, automated stop-loss
- **Alpaca MCP Integration**: Full MCP server configuration for conversational trading
- **Trade Journal**: SQLite audit trail with complete decision history
- **Streamlit Dashboard**: Real-time portfolio monitoring and analytics
- **CLI Interface**: Full command-line control over the agent

## 🏗️ Architecture

```
Scheduler (APScheduler)
    │
    ├── Data Ingestion (alpaca-py)
    │   ├── Stock Prices & Historical Bars
    │   ├── Option Chains with Greeks
    │   └── News Headlines
    │
    ├── Analysis Engine
    │   ├── Technical Indicators (pandas + numpy)
    │   ├── IV Rank & Options Analytics
    │   └── Sentiment Scoring (Gemini)
    │
    ├── Strategy Engine
    │   ├── Wheel State Machine
    │   └── LLM Reasoner (structured JSON output)
    │
    ├── Risk Manager (deterministic gates)
    │
    └── Execution Engine
        ├── Order Submission (limit orders)
        └── Fill Monitor (WebSocket)
```

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- [Alpaca Paper Trading Account](https://alpaca.markets)
- [Google Gemini API Key](https://aistudio.google.com/apikey) (optional, for LLM features)

### Installation

```bash
# Clone the repository
git clone https://github.com/prolegendluv/alphacondor.git
cd alphacondor

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -e .
```

### Configuration

```bash
# Copy example env file
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux

# Edit .env with your API keys
# ALPACA_API_KEY=your_key
# ALPACA_SECRET_KEY=your_secret
# GOOGLE_API_KEY=your_gemini_key
```

### Running the Agent

```bash
# Run one analysis cycle (recommended for first test)
alphawheel run --once

# Run in dry-run mode (analyze but don't trade)
alphawheel run --once --dry-run

# Start autonomous scheduled trading
alphawheel run

# Check portfolio status
alphawheel status

# View trade history
alphawheel history

# Analyze a specific symbol
alphawheel analyze SPY

# Run 0DTE Iron Condor agent (high margin efficiency on SPY)
alphawheel condor --once --dry-run
alphawheel condor --once
alphawheel condor

# Launch dashboard
alphawheel dashboard
```

### MCP Server Setup

The included `mcp_config.json` configures the Alpaca MCP server for use with Claude Desktop, Cursor, or other MCP-compatible tools:

```bash
# Install MCP server
pip install alpaca-mcp-server
# or
uvx alpaca-mcp-server
```

Copy `mcp_config.json` to your Claude Desktop or Cursor configuration directory.

## 🎯 Strategy Details

### Wheel Strategy Cycle

1. **Cash-Secured Put (CSP)**: Sell OTM puts (Δ ≈ 0.25, 30-45 DTE) on quality underlyings
2. **Assignment**: If assigned, acquire 100 shares at strike (cost basis = strike - premium)
3. **Covered Call (CC)**: Sell OTM calls (Δ ≈ 0.25, 30-45 DTE) against held shares
4. **Called Away**: If exercised, sell shares at strike, return to step 1

### Entry Criteria
- Trend: Not strongly bearish (price > EMA-200 or EMA-50 > EMA-200)
- RSI(14): Between 30-65
- IV Rank: ≥ 25%
- No earnings before option expiration

### Position Management
- **50% Profit Target**: Close early when 50% of max profit is achieved
- **21 DTE Roll**: Close or roll positions at 21 DTE to manage gamma risk
- **Stop Loss**: Exit if loss exceeds 200% of premium received

### Risk Guardrails
- Max 20% portfolio allocation per underlying
- Max 5 concurrent wheel positions
- Portfolio delta cap at 0.60
- Confidence floor: LLM must have ≥ 70% confidence
- Bid-ask spread filter: < 15% of mid price

### 🦅 0DTE Iron Condor Strategy (High Capital Efficiency)

To overcome the high capital intensity of Cash-Secured Puts, AlphaWheel features an autonomous **0DTE Iron Condor** engine on SPY:

- **Structure**: Sells an OTM Put Spread (income) + OTM Call Spread (income) expiring same day.
- **Capital Efficiency**: Defined-risk margin is restricted to spread width ($500 per spread) rather than full cash collateral ($29,000+ per contract).
- **Execution**: 4-leg atomic order submission with automated intraday lifecycle.
- **Intraday Profit Gating**:
  - **50% Profit Target**: Closes early when 50% of credit decays.
  - **Stop Loss**: Protects capital if debit reaches 200% of collected credit.
  - **EOD Auto-Exit**: Closes open spreads 15 minutes before market close (3:45 PM ET) to avoid overnight assignment risk.

## 📊 Technology Stack

| Component | Technology |
|-----------|------------|
| **Broker API** | Alpaca Trading API (alpaca-py) |
| **AI/LLM** | Google Gemini (structured JSON output) |
| **Technical Analysis** | pandas + numpy |
| **Options Analytics** | scipy (Black-Scholes) |
| **Scheduling** | APScheduler |
| **Data Models** | Pydantic v2 |
| **CLI** | Typer + Rich |
| **Dashboard** | Streamlit |
| **Database** | SQLite |
| **MCP** | alpaca-mcp-server |

## 📁 Project Structure

```
alphawheel/
├── src/alphawheel/
│   ├── main.py              # Wheel agent orchestrator & scheduler
│   ├── condor_agent.py      # 0DTE Iron Condor orchestrator
│   ├── config.py            # Settings & environment
│   ├── data/                # Data ingestion layer
│   ├── analysis/            # Technical & options analytics
│   ├── strategy/            # Wheel state machine, Condor engine & LLM reasoner
│   ├── risk/                # Deterministic risk gates
│   ├── execution/           # Order execution & monitoring
│   ├── journal/             # SQLite trade journal
│   └── cli/                 # CLI interface
├── dashboard/app.py         # Streamlit dashboard
├── WRITEUP.md               # One-page technical write-up for judging
├── mcp_config.json          # MCP server configuration
└── pyproject.toml           # Project dependencies
```

## 🏆 Hackathon Submission

- **Track**: 04 — Income & Portfolio Overlay Agents
- **Strategies**: Wheel Strategy (CSP + CC cycle) & 0DTE Iron Condors (SPY)
- **Alpaca Paper Account ID**: `PA3QPTGUKZ8P`
- **Technical Write-Up**: [Read the One-Page Write-Up (WRITEUP.md)](WRITEUP.md)

## 📄 License

MIT License
