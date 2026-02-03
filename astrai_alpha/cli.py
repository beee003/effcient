#!/usr/bin/env python3
"""
Astrai Alpha CLI

Usage:
    python -m astrai_alpha scan NVDA AAPL MSFT --min-confidence 0.6
    python -m astrai_alpha analyze NVDA
    python -m astrai_alpha trade NVDA --mode paper --max-position 5000
    python -m astrai_alpha portfolio
    python -m astrai_alpha backtest --tickers NVDA,AAPL --start 2024-01-01
"""

import argparse
import json
import sys
from datetime import datetime

from .sec_fetcher import SECFetcher
from .signals import SignalGenerator, SignalType
from .trader import AlphaTrader


def cmd_scan(args):
    """Scan multiple tickers for signals"""
    print(f"\n Scanning {len(args.tickers)} tickers...\n")

    fetcher = SECFetcher()
    generator = SignalGenerator(sec_fetcher=fetcher)

    signals = generator.scan_universe(
        tickers=args.tickers,
        min_confidence=args.min_confidence,
    )

    if not signals:
        print("No signals above threshold.")
        return

    print(f"Found {len(signals)} signals:\n")
    for sig in signals:
        icon = {"bullish": "", "bearish": "", "neutral": ""}[sig.signal_type.value]
        bar = "" * int(sig.confidence * 10)
        print(f"{icon} {sig.ticker:6} | {bar} {sig.confidence:.0%}")
        print(f"   {sig.rationale[:80]}...")
        print()


def cmd_analyze(args):
    """Deep analysis of single ticker"""
    print(f"\n Analyzing {args.ticker}...\n")

    fetcher = SECFetcher()
    generator = SignalGenerator(sec_fetcher=fetcher)

    signal = generator.analyze(args.ticker, sections=["1A", "7", "7A"])

    if not signal:
        print(f"Could not analyze {args.ticker}")
        return

    icon = {"bullish": "", "bearish": "", "neutral": ""}[signal.signal_type.value]
    print(f"Signal: {icon} {signal.signal_type.value.upper()}")
    print(f"Confidence: {signal.confidence:.1%}")
    print(f"\nThesis:")
    print(f"  {signal.rationale}")
    print(f"\nEvidence:")
    for i, ev in enumerate(signal.evidence[:5], 1):
        print(f"  {i}. {ev[:100]}...")
    print(f"\nSource filings: {', '.join(signal.source_filings)}")


def cmd_trade(args):
    """Execute trade based on signal"""
    print(f"\n Trading {args.ticker} ({args.mode} mode)...\n")

    fetcher = SECFetcher()
    generator = SignalGenerator(sec_fetcher=fetcher)
    trader = AlphaTrader(mode=args.mode)

    signal = generator.analyze(args.ticker)

    if not signal:
        print(f"Could not generate signal for {args.ticker}")
        return

    print(f"Signal: {signal.signal_type.value} ({signal.confidence:.1%} confidence)")
    print(f"Thesis: {signal.rationale[:100]}...")
    print()

    if signal.confidence < 0.5:
        print("Confidence too low - skipping trade")
        return

    result = trader.execute_signal(signal, max_position=args.max_position)

    if result.success:
        print(f" {result.message}")
        order = result.order
        print(f"   Order ID: {order.order_id}")
        print(f"   Filled: ${order.filled_price:.2f}")
    else:
        print(f" {result.message}")


def cmd_portfolio(args):
    """Show portfolio status"""
    trader = AlphaTrader(mode="paper")

    # Load from file if exists
    try:
        with open(args.file or "portfolio.json") as f:
            data = json.load(f)
            print(f"\n Portfolio (as of {data.get('generated_at', 'unknown')})\n")
            port = data.get("portfolio", {})
    except FileNotFoundError:
        print("\n No portfolio file found. Run some trades first.\n")
        return

    print(f"Total Value:    ${port.get('total_value', 0):,.2f}")
    print(f"Realized P&L:   ${port.get('realized_pnl', 0):,.2f}")
    print(f"Unrealized P&L: ${port.get('unrealized_pnl', 0):,.2f}")
    print(f"Total P&L:      ${port.get('total_pnl', 0):,.2f}")
    print(f"Trade Count:    {port.get('trade_count', 0)}")

    positions = port.get("positions", {})
    if positions:
        print(f"\nPositions:")
        for ticker, pos in positions.items():
            pnl_icon = "" if pos["unrealized_pnl"] >= 0 else ""
            print(f"  {ticker:6} | {pos['quantity']:4} @ ${pos['avg_cost']:.2f} | "
                  f"{pnl_icon} {pos['pnl_percent']:+.1f}%")


def cmd_backtest(args):
    """Backtest strategy on historical data"""
    print(f"\n Backtesting on {args.tickers}...\n")
    print("Backtest functionality coming soon.")
    print("Will use historical SEC filings + price data to simulate strategy.")


def main():
    parser = argparse.ArgumentParser(
        description="Astrai Alpha - AI-Native Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan tickers for signals")
    p_scan.add_argument("tickers", nargs="+", help="Tickers to scan")
    p_scan.add_argument("-m", "--min-confidence", type=float, default=0.5,
                        help="Minimum confidence threshold")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Deep analysis of ticker")
    p_analyze.add_argument("ticker", help="Ticker to analyze")

    # trade
    p_trade = subparsers.add_parser("trade", help="Execute trade on signal")
    p_trade.add_argument("ticker", help="Ticker to trade")
    p_trade.add_argument("--mode", choices=["paper", "live"], default="paper",
                         help="Trading mode")
    p_trade.add_argument("--max-position", type=float, default=10000,
                         help="Max position size in $")

    # portfolio
    p_portfolio = subparsers.add_parser("portfolio", help="Show portfolio")
    p_portfolio.add_argument("-f", "--file", help="Portfolio JSON file")

    # backtest
    p_backtest = subparsers.add_parser("backtest", help="Backtest strategy")
    p_backtest.add_argument("--tickers", help="Comma-separated tickers")
    p_backtest.add_argument("--start", help="Start date (YYYY-MM-DD)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "scan": cmd_scan,
        "analyze": cmd_analyze,
        "trade": cmd_trade,
        "portfolio": cmd_portfolio,
        "backtest": cmd_backtest,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
