"""AlphaWheel Streamlit Dashboard.

Real-time portfolio monitoring, trade history, and agent analytics.
"""

import sys
from pathlib import Path

# Add project src to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="AlphaWheel Dashboard",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_settings():
    """Load settings."""
    try:
        from alphawheel.config import get_settings
        return get_settings()
    except Exception as e:
        st.error(f"Failed to load settings: {e}")
        st.info("Make sure you have a .env file with your API keys.")
        st.stop()


def main():
    st.title("⚙️ AlphaWheel Dashboard")
    st.caption("Autonomous AI Options Trading Agent (Wheel Strategy + 0DTE Iron Condor)")

    settings = load_settings()

    # Sidebar
    with st.sidebar:
        st.header("Strategy Settings")
        st.subheader("🎡 Wheel Strategy")
        st.write(f"**Universe:** {', '.join(settings.universe[:5])}")
        st.write(f"**Target Delta:** {settings.target_delta}")
        st.write(f"**DTE Range:** {settings.min_dte}–{settings.max_dte} DTE")
        st.write(f"**Profit Target:** {settings.profit_target_pct:.0%}")
        st.write(f"**Max Positions:** {settings.max_concurrent_positions}")

        st.subheader("🦅 0DTE Iron Condor")
        st.write(f"**Underlying:** SPY")
        st.write(f"**Wing Width:** ${settings.condor_wing_width}")
        st.write(f"**Target Delta:** {settings.condor_target_delta}")
        st.write(f"**Profit Target:** {settings.condor_profit_target_pct:.0%}")
        st.write(f"**Stop Loss:** {settings.condor_stop_loss_pct:.0%}")

        if st.button("🔄 Refresh Data"):
            st.rerun()

    # Portfolio Section
    try:
        from alphawheel.data.market_data import MarketDataService
        data_service = MarketDataService(settings)
        portfolio = data_service.get_portfolio_state()

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Equity", f"${portfolio.equity:,.2f}")
        with col2:
            st.metric("Cash", f"${portfolio.cash:,.2f}")
        with col3:
            st.metric("Buying Power", f"${portfolio.buying_power:,.2f}")
        with col4:
            pl_delta = portfolio.total_pl
            st.metric("Total P&L", f"${portfolio.total_pl:,.2f}", delta=f"${pl_delta:,.2f}")

    except Exception as e:
        st.error(f"Failed to connect to Alpaca: {e}")
        portfolio = None

    st.divider()

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Positions", "📝 Trade History", "🧠 Agent Decisions", "📈 Equity Curve"])

    # Load journal data
    from alphawheel.journal.trade_log import TradeJournal
    journal = TradeJournal(settings.db_path)

    with tab1:
        st.subheader("Open Positions")
        if portfolio and portfolio.positions:
            positions_data = []
            for pos in portfolio.positions:
                positions_data.append({
                    "Symbol": pos.symbol,
                    "Qty": pos.qty,
                    "Avg Entry": f"${pos.avg_entry_price:.2f}",
                    "Current": f"${pos.current_price:.2f}",
                    "P&L": f"${pos.unrealized_pl:.2f}",
                    "P&L %": f"{pos.unrealized_plpc:.1%}",
                    "Side": pos.side,
                    "Type": pos.asset_class,
                })
            st.dataframe(pd.DataFrame(positions_data), use_container_width=True, hide_index=True)
        else:
            st.info("No open positions")

    with tab2:
        st.subheader("Trade History")
        trades = journal.get_recent_trades(limit=50)
        if trades:
            df = pd.DataFrame(trades)
            display_cols = ["timestamp", "underlying", "action", "symbol", "side", "qty", "price", "premium", "status"]
            available_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available_cols], use_container_width=True, hide_index=True)

            total_premiums = journal.get_total_premiums()
            st.metric("Total Premiums Collected", f"${total_premiums:,.2f}")
        else:
            st.info("No trades recorded yet. Run the agent to start trading.")

    with tab3:
        st.subheader("Agent Decision Log")
        decisions = journal.get_decision_history(limit=30)
        if decisions:
            decision_data = []
            for d in decisions:
                decision_data.append({
                    "Time": d["timestamp"][:19],
                    "Symbol": d["symbol"],
                    "Action": d["action_taken"] or "N/A",
                    "Executed": "✅" if d["was_executed"] else "❌",
                })
            st.dataframe(pd.DataFrame(decision_data), use_container_width=True, hide_index=True)
        else:
            st.info("No decisions recorded yet.")

    with tab4:
        st.subheader("Portfolio Equity Curve")
        history = journal.get_portfolio_history(limit=500)
        if history:
            df = pd.DataFrame(history)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            st.line_chart(df.set_index("timestamp")["equity"])
        else:
            st.info("No portfolio history yet. The agent logs snapshots during each cycle.")

    journal.close()

    # Footer
    st.divider()
    st.caption(f"AlphaWheel v0.1.0 | Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
