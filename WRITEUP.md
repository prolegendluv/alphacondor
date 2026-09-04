# 📄 AlphaWheel: Technical Write-Up
## Autonomous AI Options Trading System on Alpaca

**Project Title:** AlphaWheel  
**Track:** 04 — Income & Portfolio Overlay Agents  
**Paper Trading Account ID:** `PA3QPTGUKZ8P`  
**Hackathon:** Alpaca AI Trading Agents Hackathon (lablab.ai)

---

### 1. Executive Summary & Architecture

**AlphaWheel** is an autonomous, production-grade algorithmic options trading agent built on Alpaca's developer platform. It operates a **dual-strategy options income engine**:
1. **The Core Wheel Strategy (Cash-Secured Puts ↔ Covered Calls)**: Generates steady theta decay across blue-chip equities (`AAPL`, `MSFT`, `NVDA`, `SOFI`, `SPY`).
2. **0DTE Iron Condors (SPY)**: Provides 10x–20x greater margin efficiency by trading defined-risk 4-leg credit spreads with rapid intraday theta decay.

The core design principle is the **strict decoupling of generative AI cognition from deterministic risk guardrails**:
- **Generative AI** functions as an **analyst and advisor** (synthesizing sentiment, trend, volatility, and option chains).
- **Deterministic Python logic** acts as the **risk officer and execution firewall** (enforcing buying power, position caps, and automated trade exits).

```
                      ┌─────────────────────────────────────────┐
                      │    Alpaca Developer Infrastructure      │
                      │  Trading API | Market Data | WebSocket  │
                      └──────────────────┬──────────────────────┘
                                         │
                   ┌─────────────────────▼─────────────────────┐
                   │             Perception Layer              │
                   │  Prices (Bars) | Option Chains | News API │
                   └─────────────────────┬─────────────────────┘
                                         │
                   ┌─────────────────────▼─────────────────────┐
                   │          AI Cognitive Engine              │
                   │ Google Gemini (Strict JSON Reasoning)    │
                   └─────────────────────┬─────────────────────┘
                                         │
                   ┌─────────────────────▼─────────────────────┐
                   │        Deterministic Risk Gates           │
                   │  7 Hard Mathematical Rules (No LLM)      │
                   └─────────────────────┬─────────────────────┘
                                         │
                   ┌─────────────────────▼─────────────────────┐
                   │         Broker Execution Layer            │
                   │   Limit Orders | Multi-leg Condors        │
                   └───────────────────────────────────────────┘
```

---

### 2. AI Reasoning Logic

The cognitive layer utilizes **Google Gemini** (`gemini-3.5-flash-lite`) via the official `google-genai` SDK.

- **Market Context Bundle**: For each underlying, the system compiles:
  - Technical momentum indicators (Wilder's RSI-14, 20/50/200 EMAs, ATR-14, Bollinger Bands) computed via pure `pandas`/`numpy`.
  - Implied Volatility Rank (IV Rank) computed via Black-Scholes analytical Greeks (`scipy`).
  - Top 5 candidate option contracts filtered for liquidity (bid $\ge$ \$0.30, spread $<$ 15%).
  - Real-time market news headlines fetched via Alpaca's `NewsClient`.
  - Current portfolio financials and Wheel state stage (`IDLE`, `CSP_OPEN`, `SHARES_HELD`, `CC_OPEN`).

- **Structured Output Enforcement**: The prompt mandates a strict Pydantic JSON schema (`OptionTradeSignal`):
  - `action`: `SELL_PUT`, `SELL_CALL`, `BUY_TO_CLOSE`, `ROLL`, or `HOLD`
  - `target_strike`, `target_dte`, `limit_price`, `quantity`
  - `confidence`: Calibrated float between 0.0 and 1.0
  - `rationale`: Explainable natural language reasoning
  - `risk_factors`: Explicitly identified downside risks

- **Fail-Safe Fallback**: If LLM latency spikes or the API is unreachable, the system automatically falls back to an offline rule-based quantitative strategy without crashing.

---

### 3. Deterministic Risk Gates (The Safety Firewall)

To prevent LLM hallucinations or catastrophic trade sizing, every signal must pass **7 deterministic mathematical gates** in `RiskManager` before any order is submitted:

| Gate | Guardrail | Enforcement Logic |
|:---|:---|:---|
| **1. Confidence Floor** | Model Certainty | Hard rejection if AI confidence $< 70\%$. |
| **2. Concurrency Cap** | Portfolio Capacity | Maximum 20 simultaneous active option positions. |
| **3. Cash Collateral** | Cash-Secured Guarantee | $100\%$ collateral required for CSPs ($\text{Strike} \times 100 \times \text{Qty} \le \text{Cash}$). |
| **4. Concentration Cap** | Single Ticker Exposure | Position collateral cannot exceed configured portfolio percentage. |
| **5. Share Backing** | Covered Call Protection | Requires at least 100 long shares of underlying to sell a call. Naked calls are mathematically impossible. |
| **6. Phase Consistency** | Wheel State Machine | Enforces valid lifecycle transitions (cannot sell calls without shares; cannot sell puts when already holding short put). |
| **7. Liquidity & Spread** | Execution Quality | Filters out illiquid options with bid-ask spread $> 15\%$ or limit prices $\le \$0.05$. |

**Automated Position Management Rules:**
- **Profit Target**: Automatically buys to close positions when **50% of maximum profit** is captured.
- **Gamma Protection**: Rolls or exits contracts reaching **21 DTE** to avoid exponential assignment risks.
- **Stop Loss**: Automatic exit if position loss exceeds **200% of received premium**.
- **0DTE EOD Exit**: Automatically closes day-trade condors 15 minutes before 4:00 PM ET close.

---

### 4. Alpaca Infrastructure Implementation

AlphaWheel leverages the complete Alpaca developer ecosystem:

1. **Alpaca Trading API (`alpaca-py`)**:
   - Submits `LimitOrderRequest` orders with explicit `PositionIntent` (`SELL_TO_OPEN`, `BUY_TO_CLOSE`, `BUY_TO_OPEN`, `SELL_TO_CLOSE`).
   - Retrieves real-time account balances, buying power, and active positions.
   - Synchronizes broker-level state with internal SQLite journal.

2. **Alpaca Market Data API**:
   - Stock quotes and historical daily bars via `StockHistoricalDataClient`.
   - Option chains and contract snapshots via `OptionHistoricalDataClient` and `TradingClient`.
   - Real-time company news via `NewsClient`.

3. **Alpaca TradingStream (WebSocket)**:
   - Event-driven fill monitoring using `TradingStream`. Asynchronously reacts to `fill`, `canceled`, and `rejected` updates.

4. **Alpaca Model Context Protocol (MCP)**:
   - Configured via `mcp_config.json` running `uvx alpaca-mcp-server`.
   - Enables LLM assistants (Claude, Cursor, ChatGPT) to converse with the paper trading account using structured tools across `account`, `trading`, `stock-data`, `options-data`, and `news`.

5. **Observability & Interfaces**:
   - **Streamlit Web Dashboard**: Real-time KPI cards, open positions table, trade history, and equity curve.
   - **Rich CLI**: Commands for `run`, `status`, `history`, `analyze`, and `condor`.
   - **SQLite Trade Journal**: Relational audit trail capturing every decision, context, risk validation, and execution.
