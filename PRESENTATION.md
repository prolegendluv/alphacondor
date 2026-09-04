# 🎯 AlphaCondor: Slide Presentation & Video Script
## Autonomous AI Options Trading System on Alpaca

**Project Title:** AlphaCondor  
**Track:** 04 — Income & Portfolio Overlay Agents  
**Paper Trading Account ID:** `PA3QPTGUKZ8P` ($100,000 Starting Balance)  
**GitHub Repository:** [https://github.com/prolegendluv/alphacondor](https://github.com/prolegendluv/alphacondor)  
**PowerPoint File:** [`presentation.pptx`](presentation.pptx) (Included in repo)

---

## 📽️ Video Presentation Script (2–3 Minutes)

> **Instructions for Recording:**
> You can record your screen using OBS, Loom, or Windows Game Bar (`Win + G`).
> Keep your Streamlit dashboard (`http://localhost:8501`) or the PowerPoint slides open on screen, and read the script below.

### [0:00 – 0:30] Introduction & Problem
> *"Hello judges! Welcome to our submission for the Alpaca AI Trading Agents Hackathon: **AlphaCondor**, an autonomous AI options trading system. 
> In quantitative finance, the biggest barrier for retail options income has always been capital efficiency and risk. Selling traditional Cash-Secured Puts on blue chips like Apple or Nvidia locks up thirty to sixty thousand dollars in cash collateral per contract for small nominal yields. Furthermore, connecting generative AI directly to broker execution without safeguards is dangerous due to hallucinations and leverage risk.
> AlphaCondor solves this by combining the institutional **Wheel Strategy** with a high-margin-efficiency **0DTE Iron Condor** engine, governed by a deterministic mathematical firewall."*

### [0:30 – 1:15] Architecture & Decoupled AI Firewall
> *"Our core design principle is the **strict decoupling of AI cognition from risk management**:
> Google Gemini 3.5 acts strictly as an **analyst and advisor**. It evaluates real-time market data from Alpaca—including historical daily bars, Wilder's RSI, 20, 50, and 200-day EMAs, Black-Scholes IV Rank, and live company news from Alpaca's NewsClient. 
> Gemini outputs structured Pydantic trade signals with explicit confidence scores and risk rationales. 
> But no order is placed until the signal passes through **7 deterministic Python risk gates**: 
> 1. A 70% confidence floor.
> 2. Max concurrent position caps.
> 3. Full cash collateral validation.
> 4. Ticker concentration limits.
> 5. 100-share backing for covered calls—making naked calls mathematically impossible.
> 6. State machine phase consistency.
> 7. Strict liquidity and bid-ask spread filters."*

### [1:15 – 2:00] Dual-Strategy & 0DTE Iron Condor Edge
> *"What sets AlphaCondor apart is its **Dual-Strategy Engine**:
> While our Wheel strategy harvests conservative 20% to 35% annualized theta decay on mega-caps, our autonomous **0DTE Iron Condor** engine on SPY unlocks 10x to 20x greater margin efficiency. 
> Instead of locking up $30,000 for a single put, a 4-leg Iron Condor spread has defined risk capped at just $500 margin. 
> In our 4-day backtest on SPY, this generated up to 21 times more profit with the exact same $100,000 capital.
> All positions are managed by automated rules: taking profits at 50%, cutting losses at 200%, and closing 0DTE spreads 15 minutes before market close to eliminate overnight assignment risk."*

### [2:00 – 2:45] Alpaca Developer Stack & Live Demo
> *(Switch screen to the Streamlit Dashboard at `http://localhost:8501` and terminal)*
> *"We built deeply across Alpaca's entire developer ecosystem:
> - The **Alpaca Trading API** via `alpaca-py` handles limit orders with explicit position intent.
> - **Alpaca Market Data** feeds historical bars and options chains with Greeks.
> - **TradingStream WebSockets** monitor fills in real-time.
> - The **Alpaca MCP Server** enables conversational control via Claude or Cursor.
> - And our live **Streamlit Dashboard** provides real-time portfolio KPI monitoring, open position tables, and an equity curve backed by an SQLite audit journal.
> Our fresh competition account **PA3QPTGUKZ8P** is live with $100,000 equity, 19 out of 19 unit tests are passing, and our full codebase is public on GitHub. Thank you!"*

---

## 📊 Slide-by-Slide Presentation Deck

### Slide 1: Title Slide
* **Title:** AlphaCondor
* **Subtitle:** Autonomous AI Options Trading System on Alpaca
* **Focus:** Institutional Wheel Strategy + High-Efficiency 0DTE Iron Condors
* **Hackathon Track:** Track 04 — Income & Portfolio Overlay Agents
* **Paper Account ID:** `PA3QPTGUKZ8P` ($100,000 Starting Balance)
* **GitHub:** `https://github.com/prolegendluv/alphacondor`

---

### Slide 2: The Retail Options Dilemma (Problem & Solution)
* **The Retail Options Problem:**
  - **Capital Lockup:** Cash-Secured Puts require $30K–$60K collateral per contract for small yields.
  - **Unconstrained LLM Risk:** Direct LLM execution leads to hallucinations and leverage blowups.
  - **Rapid Gamma Risk:** 0DTE options decay rapidly but carry dangerous tail risk without active management.
* **The AlphaCondor Solution:**
  - **Strict Decoupling:** AI functions as an advisor; deterministic Python code functions as an immutable firewall.
  - **Dual-Engine Model:** Wheel strategy on equities + defined-risk 0DTE credit spreads on SPY.
  - **Full Observability:** Live Streamlit dashboard, Rich CLI, SQLite audit journal, and Alpaca MCP server.

---

### Slide 3: End-to-End System Pipeline
1. **Perception:** Stock prices, OHLCV bars, option chains with Greeks, and company news headlines via `alpaca-py`.
2. **Analysis Engine:** Wilder's RSI(14), 20/50/200 EMAs, ATR-14, Bollinger Bands, and Black-Scholes IV Rank.
3. **AI Cognition:** Google Gemini 3.5 structured Pydantic JSON reasoning (`OptionTradeSignal`) with confidence scoring.
4. **Deterministic Risk Firewall:** 7 immutable mathematical gates enforcing sizing, cash collateral, and share backing.
5. **Execution & WebSockets:** Limit orders with explicit `PositionIntent` and `TradingStream` WebSocket fill monitoring.

---

### Slide 4: Dual Options Strategy Engine
* **Strategy 1: The Core Wheel**
  - Underlying Universe: AAPL, MSFT, NVDA, SOFI, SPY.
  - Cash-Secured Put (CSP) $\to$ Assignment $\to$ Covered Call (CC) $\to$ Called Away.
  - Generates steady, conservative options yield (20%–35% annualized).
* **Strategy 2: 0DTE Iron Condors**
  - Underlying: SPY (highest liquidity broad market ETF).
  - Defined-risk 4-leg credit spread: Buy Put + Sell Put + Sell Call + Buy Call.
  - Margin capped at spread width ($500 per spread) rather than strike collateral ($30,000+).
  - Yields 10x–20x greater capital efficiency and rapid same-day theta capture.

---

### Slide 5: The 7 Deterministic Risk Gates
| Gate | Guardrail | Mathematical Rule |
|:---|:---|:---|
| **1. Confidence Floor** | Model Certainty | Hard rejection if Gemini confidence $< 70\%$. |
| **2. Concurrency Cap** | Portfolio Capacity | Maximum 20 simultaneous active option positions. |
| **3. Cash Collateral** | Cash-Secured Guarantee | $\text{Strike} \times 100 \times \text{Qty} \le \text{Available Cash}$. |
| **4. Concentration Cap** | Single Ticker Exposure | Collateral per ticker capped at max portfolio percentage. |
| **5. Share Backing** | Covered Call Rule | $\ge 100$ shares owned. Naked calls are mathematically impossible. |
| **6. Phase Consistency** | Wheel State Machine | Enforces valid lifecycle transitions between CSP and CC. |
| **7. Liquidity & Spread** | Execution Quality | Bid-ask spread $< 15\%$ and limit price $> \$0.05$. |

---

### Slide 6: Automated Position Lifecycle Management
* **🎯 50% Profit Target:** Automatically buys to close positions once 50% of maximum profit is captured, accelerating capital recycling.
* **⏳ 21 DTE Roll Threshold:** Rolls or closes positions reaching 21 DTE to eliminate exponential assignment and gamma risk.
* **🛡️ 200% Stop Loss:** Liquidates position immediately if market moves cause a loss exceeding 200% of received premium.
* **⏰ 3:45 PM EOD Exit:** Closes open 0DTE Iron Condor spreads 15 minutes before the bell to avoid overnight assignment.

---

### Slide 7: Alpaca Developer Stack & MCP Server
* **Alpaca Trading API (`alpaca-py`):** Explicit `PositionIntent` order routing and account synchronization.
* **Alpaca Market Data:** Historical bars, real-time quotes, and option chains with Black-Scholes Greeks.
* **Alpaca TradingStream:** Asynchronous WebSocket streaming reacting to fills and order updates.
* **Alpaca MCP Server:** Configured via `mcp_config.json` running `uvx alpaca-mcp-server` for Claude Desktop and Cursor.
* **Streamlit Web Dashboard:** Real-time KPI metrics, open position monitors, and equity curve visualizations.
* **Rich CLI & SQLite Audit Journal:** Terminal commands and relational audit trails for every agent decision.

---

### Slide 8: Verification & Results
* **19 / 19 Unit Tests Passing** (`pytest tests/ -v`).
* **$100,000 Pristine Competition Account** (`PA3QPTGUKZ8P`) ready for judging.
* **7x–21x Margin Efficiency Multiplier** demonstrated on SPY credit spreads.
* **Full Open-Source Package** with comprehensive documentation and one-page technical write-up.
