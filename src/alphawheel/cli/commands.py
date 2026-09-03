"""AlphaWheel CLI interface.

Provides commands to run the agent, check status, and view history.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

app = typer.Typer(
    name="alphawheel",
    help="AlphaWheel - Autonomous AI Wheel Strategy Trading Agent",
    add_completion=False,
)
console = Console()


def _setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _get_settings():
    """Load settings with error handling."""
    try:
        from alphawheel.config import get_settings
        return get_settings()
    except Exception as e:
        console.print(f"[red]Error loading settings: {e}[/red]")
        console.print("Make sure you have a .env file with your API keys.")
        console.print("See .env.example for the required variables.")
        raise typer.Exit(1)


@app.command()
def run(
    once: bool = typer.Option(False, "--once", help="Run one analysis cycle and exit"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyze but don't execute trades"),
    force: bool = typer.Option(False, "--force", "-f", help="Force run even if market is closed"),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level"),
) -> None:
    """Start the autonomous trading agent."""
    _setup_logging(log_level)
    settings = _get_settings()

    console.print(Panel(
        "[bold green]AlphaWheel[/bold green] - Autonomous AI Wheel Strategy Agent\n"
        f"Mode: {'[yellow]DRY RUN[/yellow]' if dry_run else '[green]LIVE PAPER TRADING[/green]'}\n"
        f"Schedule: {', '.join(settings.schedule_times)} ET\n"
        f"Universe: {', '.join(settings.universe[:5])}{'...' if len(settings.universe) > 5 else ''}",
        title="Starting Agent",
        border_style="green",
    ))

    from alphawheel.main import AlphaWheelAgent
    agent = AlphaWheelAgent(settings, dry_run=dry_run)

    if once:
        agent.run_cycle(force=force)
    else:
        agent.start()


@app.command()
def status(
    log_level: str = typer.Option("WARNING", "--log-level", help="Logging level"),
) -> None:
    """Show current portfolio and wheel states."""
    _setup_logging(log_level)
    settings = _get_settings()

    from alphawheel.data.market_data import MarketDataService
    data_service = MarketDataService(settings)

    try:
        portfolio = data_service.get_portfolio_state()
    except Exception as e:
        console.print(f"[red]Failed to connect to Alpaca: {e}[/red]")
        raise typer.Exit(1)

    # Account Summary
    console.print(Panel(
        f"Equity: [green]${portfolio.equity:,.2f}[/green]\n"
        f"Cash: ${portfolio.cash:,.2f}\n"
        f"Buying Power: ${portfolio.buying_power:,.2f}\n"
        f"P&L: {'[green]' if portfolio.total_pl >= 0 else '[red]'}${portfolio.total_pl:,.2f}",
        title="Portfolio Summary",
        border_style="blue",
    ))

    # Positions Table
    if portfolio.positions:
        table = Table(title="Open Positions", box=box.ROUNDED)
        table.add_column("Symbol", style="cyan")
        table.add_column("Qty", justify="right")
        table.add_column("Avg Entry", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("P&L %", justify="right")
        table.add_column("Type")

        for pos in portfolio.positions:
            pl_color = "green" if pos.unrealized_pl >= 0 else "red"
            table.add_row(
                pos.symbol,
                str(pos.qty),
                f"${pos.avg_entry_price:.2f}",
                f"${pos.current_price:.2f}",
                f"[{pl_color}]${pos.unrealized_pl:.2f}[/{pl_color}]",
                f"[{pl_color}]{pos.unrealized_plpc:.1%}[/{pl_color}]",
                pos.asset_class,
            )
        console.print(table)
    else:
        console.print("[dim]No open positions[/dim]")


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of trades to show"),
    log_level: str = typer.Option("WARNING", "--log-level", help="Logging level"),
) -> None:
    """Show trade history from the journal."""
    _setup_logging(log_level)
    settings = _get_settings()

    from alphawheel.journal.trade_log import TradeJournal
    journal = TradeJournal(settings.db_path)

    trades = journal.get_recent_trades(limit=limit)
    if not trades:
        console.print("[dim]No trades recorded yet.[/dim]")
        return

    table = Table(title=f"Recent Trades (last {limit})", box=box.ROUNDED)
    table.add_column("Time", style="dim")
    table.add_column("Symbol", style="cyan")
    table.add_column("Action", style="bold")
    table.add_column("Side")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Premium", justify="right", style="green")
    table.add_column("Status")

    for trade in trades:
        table.add_row(
            trade["timestamp"][:19],
            trade["symbol"][:20],
            trade["action"],
            trade["side"],
            str(trade["qty"]),
            f"${trade['price']:.2f}" if trade["price"] else "-",
            f"${trade['premium']:.2f}" if trade["premium"] else "-",
            trade["status"] or "-",
        )
    console.print(table)

    total_premiums = journal.get_total_premiums()
    console.print(f"\nTotal premiums collected: [green]${total_premiums:,.2f}[/green]")
    journal.close()


@app.command()
def analyze(
    symbol: str = typer.Argument(..., help="Symbol to analyze"),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level"),
) -> None:
    """Run analysis for a specific symbol."""
    _setup_logging(log_level)
    settings = _get_settings()

    console.print(f"\n[bold]Analyzing {symbol.upper()}...[/bold]\n")

    from alphawheel.data.market_data import MarketDataService
    from alphawheel.analysis.technicals import compute_technicals
    from alphawheel.analysis.options_analytics import rank_contracts_for_display

    data_service = MarketDataService(settings)

    # Technical Analysis
    try:
        bars = data_service.get_stock_bars(symbol.upper())
        technicals = compute_technicals(symbol.upper(), bars)

        console.print(Panel(
            f"Price: ${technicals.price:.2f}\n"
            f"Trend: {technicals.trend.value.upper()}\n"
            f"Momentum: {technicals.momentum.value.upper()}\n"
            f"RSI(14): {technicals.rsi_14:.1f}" if technicals.rsi_14 else "RSI: N/A" + "\n"
            f"EMA(50): ${technicals.ema_50:.2f}" if technicals.ema_50 else "" + "\n"
            f"EMA(200): ${technicals.ema_200:.2f}" if technicals.ema_200 else "",
            title=f"Technical Analysis - {symbol.upper()}",
            border_style="cyan",
        ))
    except Exception as e:
        console.print(f"[red]Technical analysis failed: {e}[/red]")

    # Options Chain
    try:
        put_contracts = data_service.get_option_chain(
            symbol.upper(), "put",
            settings.min_dte, settings.max_dte,
        )
        top_puts = rank_contracts_for_display(put_contracts, settings.target_delta)

        if top_puts:
            table = Table(title="Top Put Contracts", box=box.ROUNDED)
            table.add_column("Contract", style="cyan")
            table.add_column("Strike", justify="right")
            table.add_column("DTE", justify="right")
            table.add_column("Delta", justify="right")
            table.add_column("Bid", justify="right")
            table.add_column("Ask", justify="right")
            table.add_column("IV", justify="right")

            for c in top_puts:
                table.add_row(
                    c.symbol,
                    f"${c.strike:.2f}",
                    str(c.dte),
                    f"{c.delta:.3f}" if c.delta else "N/A",
                    f"${c.bid:.2f}",
                    f"${c.ask:.2f}",
                    f"{c.implied_volatility:.1%}" if c.implied_volatility else "N/A",
                )
            console.print(table)
        else:
            console.print("[dim]No suitable put contracts found[/dim]")
    except Exception as e:
        console.print(f"[red]Options analysis failed: {e}[/red]")


@app.command()
def condor(
    once: bool = typer.Option(False, "--once", help="Run one condor analysis cycle and exit"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyze but don't execute trades"),
    force: bool = typer.Option(False, "--force", "-f", help="Force run even if market is closed"),
    symbol: str = typer.Option("SPY", "--symbol", "-s", help="Underlying symbol (default: SPY)"),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level"),
) -> None:
    """Start the 0DTE Iron Condor high-margin-efficiency trading agent."""
    _setup_logging(log_level)
    settings = _get_settings()

    console.print(Panel(
        f"[bold cyan]AlphaWheel — 0DTE Iron Condor Agent[/bold cyan]\n"
        f"Underlying: [bold]{symbol.upper()}[/bold]\n"
        f"Mode: {'[yellow]DRY RUN[/yellow]' if dry_run else '[green]LIVE PAPER TRADING[/green]'}\n"
        f"Wing Width: ${settings.condor_wing_width} | Target Delta: {settings.condor_target_delta}\n"
        f"Profit Target: {settings.condor_profit_target_pct:.0%} | Stop Loss: {settings.condor_stop_loss_pct:.0%}\n"
        f"Max Capital: {settings.condor_max_capital_pct:.0%}",
        title="Starting 0DTE Condor Agent",
        border_style="cyan",
    ))

    from alphawheel.condor_agent import CondorAgent
    agent = CondorAgent(settings, dry_run=dry_run, symbol=symbol.upper())

    if once:
        agent.run_once(force=force)
    else:
        agent.start()


@app.command()
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess
    dashboard_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    if not dashboard_path.exists():
        # Try from project root
        dashboard_path = Path("dashboard/app.py")
    console.print(f"[green]Launching dashboard from {dashboard_path}...[/green]")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path)])


if __name__ == "__main__":
    app()

