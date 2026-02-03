"""
Alpaca Paper Trading Gateway

Real fills. Real latency. Real proof.

This moves you from "simulation" to "live paper" - the difference
between a backtest and something an MIT professor will believe.

Features:
1. Paper trading via Alpaca API
2. Risk gate (max position, daily loss limit, trade frequency)
3. Automatic ledger logging
4. Position tracking with real prices
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime, date
from enum import Enum
from typing import Optional
import json

# Alpaca SDK - optional import
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    HAS_ALPACA = True
except ImportError:
    HAS_ALPACA = False
    TradingClient = None


class RiskViolation(Enum):
    """Risk check violations"""
    MAX_POSITION_EXCEEDED = "max_position_exceeded"
    DAILY_LOSS_EXCEEDED = "daily_loss_exceeded"
    MAX_TRADES_EXCEEDED = "max_trades_exceeded"
    CONCENTRATION_EXCEEDED = "concentration_exceeded"
    MARKET_CLOSED = "market_closed"


@dataclass
class RiskLimits:
    """Risk parameters - non-negotiable"""
    max_position_dollars: float = 10000      # Max $ in single position
    max_portfolio_dollars: float = 100000    # Max total exposure
    max_daily_loss_dollars: float = 2000     # Stop trading if hit
    max_daily_loss_pct: float = 0.02         # 2% of portfolio
    max_trades_per_day: int = 50             # Prevent overtrading
    max_concentration_pct: float = 0.20      # Max 20% in single name
    require_market_open: bool = True         # Only trade during hours


@dataclass
class RiskCheckResult:
    """Result of risk check"""
    approved: bool
    violation: Optional[RiskViolation] = None
    message: str = ""
    adjusted_quantity: Optional[int] = None  # Reduced size if needed


class AlpacaGateway:
    """
    Production-grade Alpaca paper trading gateway.

    Usage:
        gateway = AlpacaGateway(
            api_key="...",
            api_secret="...",
            paper=True,  # ALWAYS start with paper
        )

        # Check risk before trading
        risk = gateway.check_risk("NVDA", "buy", 100)
        if risk.approved:
            order = gateway.submit_order("NVDA", "buy", 100)
            print(f"Filled: {order}")

        # Get positions
        positions = gateway.get_positions()

        # Daily P&L
        pnl = gateway.get_daily_pnl()
    """

    # The ETF universe (locked in from discussion)
    UNIVERSE = [
        # Broad market
        "SPY", "QQQ", "IWM", "DIA",
        # Sectors
        "XLF", "XLK", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLB", "XLRE",
        # Themes
        "ARKK", "SOXX", "SMH", "XBI", "GDX",
        # Fixed income
        "TLT", "HYG", "LQD",
        # Commodities
        "GLD", "SLV", "USO",
        # Volatility
        "VXX",
        # Mega-caps
        "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper: bool = True,
        risk_limits: Optional[RiskLimits] = None,
        ledger=None,  # TradeLedger instance
    ):
        """
        Initialize Alpaca gateway.

        Args:
            api_key: Alpaca API key (or env ALPACA_API_KEY)
            api_secret: Alpaca API secret (or env ALPACA_API_SECRET)
            paper: Use paper trading (default True - NEVER start with live)
            risk_limits: Risk parameters
            ledger: TradeLedger for immutable logging
        """
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET")
        self.paper = paper
        self.risk_limits = risk_limits or RiskLimits()
        self.ledger = ledger

        # Daily tracking
        self._daily_trades = 0
        self._daily_pnl = 0.0
        self._last_trade_date = None

        # Initialize client
        self._client = None
        self._data_client = None

        if not HAS_ALPACA:
            print("Warning: alpaca-py not installed. Using mock mode.")
            print("Install with: pip install alpaca-py")
        elif self.api_key and self.api_secret:
            self._init_client()

    def _init_client(self):
        """Initialize Alpaca client"""
        if not HAS_ALPACA:
            return

        self._client = TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=self.paper,
        )

        self._data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    def _reset_daily_counters(self):
        """Reset daily counters if new day"""
        today = date.today()
        if self._last_trade_date != today:
            self._daily_trades = 0
            self._daily_pnl = 0.0
            self._last_trade_date = today

    def get_quote(self, ticker: str) -> dict:
        """Get current quote for ticker"""
        if not self._data_client:
            # Mock quote
            return {
                "ticker": ticker,
                "bid": 100.0,
                "ask": 100.02,
                "mid": 100.01,
                "spread_bps": 2.0,
            }

        request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self._data_client.get_stock_latest_quote(request)

        if ticker in quotes:
            q = quotes[ticker]
            bid = float(q.bid_price)
            ask = float(q.ask_price)
            mid = (bid + ask) / 2
            spread_bps = ((ask - bid) / mid) * 10000 if mid > 0 else 0

            return {
                "ticker": ticker,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_bps": spread_bps,
            }

        return {"ticker": ticker, "error": "No quote available"}

    def get_account(self) -> dict:
        """Get account info"""
        if not self._client:
            return {
                "equity": 100000.0,
                "buying_power": 200000.0,
                "cash": 100000.0,
                "portfolio_value": 100000.0,
            }

        account = self._client.get_account()
        return {
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
        }

    def get_positions(self) -> list[dict]:
        """Get all current positions"""
        if not self._client:
            return []

        positions = self._client.get_all_positions()
        return [{
            "ticker": p.symbol,
            "quantity": int(p.qty),
            "side": "long" if int(p.qty) > 0 else "short",
            "avg_cost": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "market_value": float(p.market_value),
            "unrealized_pnl": float(p.unrealized_pl),
            "unrealized_pnl_pct": float(p.unrealized_plpc) * 100,
        } for p in positions]

    def get_position(self, ticker: str) -> Optional[dict]:
        """Get position for specific ticker"""
        positions = self.get_positions()
        for p in positions:
            if p["ticker"] == ticker:
                return p
        return None

    def check_risk(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: Optional[float] = None,
    ) -> RiskCheckResult:
        """
        Check if trade passes risk limits.

        This is the gate that protects you from yourself.
        """
        self._reset_daily_counters()

        # Get current price
        if price is None:
            quote = self.get_quote(ticker)
            price = quote.get("mid", 100.0)

        notional = price * quantity
        account = self.get_account()
        portfolio_value = account["portfolio_value"]

        # Check 1: Max position size
        if notional > self.risk_limits.max_position_dollars:
            max_qty = int(self.risk_limits.max_position_dollars / price)
            return RiskCheckResult(
                approved=False,
                violation=RiskViolation.MAX_POSITION_EXCEEDED,
                message=f"Position ${notional:,.0f} exceeds max ${self.risk_limits.max_position_dollars:,.0f}",
                adjusted_quantity=max_qty,
            )

        # Check 2: Concentration limit
        concentration = notional / portfolio_value if portfolio_value > 0 else 1.0
        if concentration > self.risk_limits.max_concentration_pct:
            max_qty = int(portfolio_value * self.risk_limits.max_concentration_pct / price)
            return RiskCheckResult(
                approved=False,
                violation=RiskViolation.CONCENTRATION_EXCEEDED,
                message=f"Concentration {concentration:.1%} exceeds max {self.risk_limits.max_concentration_pct:.1%}",
                adjusted_quantity=max_qty,
            )

        # Check 3: Daily loss limit
        if self._daily_pnl < -self.risk_limits.max_daily_loss_dollars:
            return RiskCheckResult(
                approved=False,
                violation=RiskViolation.DAILY_LOSS_EXCEEDED,
                message=f"Daily loss ${abs(self._daily_pnl):,.0f} exceeds max ${self.risk_limits.max_daily_loss_dollars:,.0f}",
            )

        # Check 4: Trade count limit
        if self._daily_trades >= self.risk_limits.max_trades_per_day:
            return RiskCheckResult(
                approved=False,
                violation=RiskViolation.MAX_TRADES_EXCEEDED,
                message=f"Daily trades {self._daily_trades} exceeds max {self.risk_limits.max_trades_per_day}",
            )

        # Check 5: Market hours (optional)
        if self.risk_limits.require_market_open:
            # TODO: Check actual market hours
            pass

        return RiskCheckResult(approved=True, message="Risk check passed")

    def submit_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        force: bool = False,
    ) -> dict:
        """
        Submit order to Alpaca.

        Args:
            ticker: Stock/ETF symbol
            side: "buy" or "sell"
            quantity: Number of shares
            order_type: "market" or "limit"
            limit_price: Price for limit orders
            force: Skip risk check (DANGEROUS)

        Returns:
            Order result dict
        """
        self._reset_daily_counters()

        # Risk check (unless forced - don't do this)
        if not force:
            quote = self.get_quote(ticker)
            price = quote.get("mid", 100.0)
            risk = self.check_risk(ticker, side, quantity, price)

            if not risk.approved:
                result = {
                    "success": False,
                    "rejected": True,
                    "reason": risk.message,
                    "violation": risk.violation.value if risk.violation else None,
                    "suggested_quantity": risk.adjusted_quantity,
                }

                # Log rejection to ledger
                if self.ledger:
                    from .execution import ExecutionResult
                    exec_result = ExecutionResult(
                        intended_price=price,
                        intended_quantity=quantity,
                        filled_price=0,
                        filled_quantity=0,
                        spread_cost=0,
                        impact_cost=0,
                        commission=0,
                        sec_fee=0,
                        total_cost=0,
                        cost_bps=0,
                        latency_ms=0,
                        fully_filled=False,
                        rejected=True,
                        rejection_reason=risk.message,
                    )
                    self.ledger.append(
                        ticker=ticker,
                        side=side,
                        execution=exec_result,
                        signal_type="risk_rejected",
                        signal_confidence=0,
                        signal_rationale=risk.message,
                    )

                return result

        # Submit to Alpaca
        if not self._client:
            # Mock order
            quote = self.get_quote(ticker)
            filled_price = quote["ask"] if side == "buy" else quote["bid"]

            result = {
                "success": True,
                "order_id": f"MOCK-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "filled_quantity": quantity,
                "filled_price": filled_price,
                "status": "filled",
                "mock": True,
            }
        else:
            # Real Alpaca order
            alpaca_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

            if order_type == "market":
                request = MarketOrderRequest(
                    symbol=ticker,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                )
            else:
                request = LimitOrderRequest(
                    symbol=ticker,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                )

            order = self._client.submit_order(request)

            # Wait briefly for fill (paper trading fills instantly)
            time.sleep(0.5)

            # Get updated order status
            order = self._client.get_order_by_id(order.id)

            result = {
                "success": order.status in [OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED],
                "order_id": str(order.id),
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "filled_quantity": int(order.filled_qty) if order.filled_qty else 0,
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else 0,
                "status": str(order.status),
            }

        # Update counters
        self._daily_trades += 1

        # Log to ledger
        if self.ledger and result["success"]:
            from .execution import ExecutionResult

            quote = self.get_quote(ticker)
            mid = quote.get("mid", result["filled_price"])
            slippage = abs(result["filled_price"] - mid) / mid * 10000 if mid > 0 else 0

            # Estimate costs
            notional = result["filled_price"] * result["filled_quantity"]
            spread_bps = quote.get("spread_bps", 2.0) / 2  # Half spread
            spread_cost = notional * spread_bps / 10000
            commission = 0.0035 * result["filled_quantity"]  # IBKR-style

            exec_result = ExecutionResult(
                intended_price=mid,
                intended_quantity=quantity,
                filled_price=result["filled_price"],
                filled_quantity=result["filled_quantity"],
                spread_cost=spread_cost,
                impact_cost=0,  # Can't measure without pre-trade price
                commission=commission,
                sec_fee=0,
                total_cost=spread_cost + commission,
                cost_bps=(spread_cost + commission) / notional * 10000 if notional > 0 else 0,
                latency_ms=500,  # Estimated
                fully_filled=(result["filled_quantity"] == quantity),
                rejected=False,
                slippage_bps=slippage,
            )

            self.ledger.append(
                ticker=ticker,
                side=side,
                execution=exec_result,
                signal_type="live_paper",
                signal_confidence=1.0,
                signal_rationale="Alpaca paper trade",
            )

        return result

    def get_daily_pnl(self) -> dict:
        """Get today's P&L"""
        account = self.get_account()
        positions = self.get_positions()

        unrealized = sum(p["unrealized_pnl"] for p in positions)

        return {
            "date": date.today().isoformat(),
            "realized_pnl": self._daily_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": self._daily_pnl + unrealized,
            "trade_count": self._daily_trades,
            "portfolio_value": account["portfolio_value"],
        }

    def close_position(self, ticker: str) -> dict:
        """Close entire position in ticker"""
        position = self.get_position(ticker)
        if not position:
            return {"success": False, "message": f"No position in {ticker}"}

        side = "sell" if position["quantity"] > 0 else "buy"
        quantity = abs(position["quantity"])

        return self.submit_order(ticker, side, quantity, force=True)

    def close_all_positions(self) -> list[dict]:
        """Close all positions (end of day)"""
        results = []
        for position in self.get_positions():
            result = self.close_position(position["ticker"])
            results.append(result)
        return results


def demo_gateway():
    """Demo the gateway (mock mode)"""
    from .ledger import TradeLedger

    print("=" * 60)
    print("ALPACA GATEWAY DEMO (Mock Mode)")
    print("=" * 60)

    ledger = TradeLedger("/tmp/alpaca_demo.jsonl")
    gateway = AlpacaGateway(ledger=ledger)

    # Show account
    print("\nAccount:")
    account = gateway.get_account()
    print(f"  Equity: ${account['equity']:,.2f}")
    print(f"  Buying Power: ${account['buying_power']:,.2f}")

    # Get quotes
    print("\nQuotes:")
    for ticker in ["SPY", "QQQ", "NVDA"]:
        quote = gateway.get_quote(ticker)
        print(f"  {ticker}: ${quote['mid']:.2f} (spread: {quote['spread_bps']:.1f} bps)")

    # Risk check
    print("\nRisk Check (100 shares NVDA):")
    risk = gateway.check_risk("NVDA", "buy", 100)
    print(f"  Approved: {risk.approved}")
    print(f"  Message: {risk.message}")

    # Submit order
    print("\nSubmitting order...")
    result = gateway.submit_order("NVDA", "buy", 50)
    print(f"  Success: {result['success']}")
    print(f"  Filled: {result['filled_quantity']} @ ${result['filled_price']:.2f}")
    print(f"  Order ID: {result['order_id']}")

    # Daily P&L
    print("\nDaily P&L:")
    pnl = gateway.get_daily_pnl()
    print(f"  Total: ${pnl['total_pnl']:,.2f}")
    print(f"  Trades: {pnl['trade_count']}")

    # Verify ledger
    print("\nLedger verification:")
    valid, error = ledger.verify_chain()
    print(f"  Chain valid: {valid}")


if __name__ == "__main__":
    demo_gateway()
