"""
Alpha Trader - Execute trades based on signals

Supports paper trading (Alpaca) and live trading (IBKR).
Uses position sizing based on signal confidence.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .signals import Signal, SignalType


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class Order:
    """Trade order"""
    order_id: str
    ticker: str
    side: OrderSide
    quantity: int
    price: Optional[float] = None  # None = market order
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_at: Optional[str] = None
    signal_id: Optional[str] = None


@dataclass
class Position:
    """Current position in a security"""
    ticker: str
    quantity: int
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    pnl_percent: float


@dataclass
class TradeResult:
    """Result of a trade execution"""
    order: Order
    success: bool
    message: str


class AlphaTrader:
    """
    AI-powered trading executor.

    Features:
    - Kelly criterion position sizing
    - Signal-confidence based allocation
    - Paper and live trading modes
    - Portfolio tracking

    Usage:
        trader = AlphaTrader(mode="paper", api_key="...")
        signal = generator.analyze("NVDA")
        if signal.confidence > 0.7:
            result = trader.execute_signal(signal, max_position=10000)
    """

    def __init__(
        self,
        mode: str = "paper",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        max_portfolio_value: float = 100000,
        max_position_pct: float = 0.1,  # Max 10% in single position
    ):
        """
        Initialize trader.

        Args:
            mode: "paper" or "live"
            api_key: Broker API key (or from env ALPACA_API_KEY)
            api_secret: Broker API secret
            max_portfolio_value: Total portfolio value
            max_position_pct: Max % of portfolio in single position
        """
        self.mode = mode
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET")
        self.max_portfolio = max_portfolio_value
        self.max_position_pct = max_position_pct

        # Track positions and orders
        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._order_counter = 0

        # P&L tracking
        self._realized_pnl = 0.0
        self._trade_history: list[dict] = []

    def _generate_order_id(self) -> str:
        """Generate unique order ID"""
        self._order_counter += 1
        return f"ORD-{datetime.now().strftime('%Y%m%d')}-{self._order_counter:04d}"

    def _get_price(self, ticker: str) -> float:
        """Get current price for ticker (mock for now)"""
        # In production, would call market data API
        # For paper trading, use mock prices
        mock_prices = {
            "NVDA": 875.50,
            "AAPL": 182.30,
            "MSFT": 415.20,
            "GOOGL": 175.80,
            "TSLA": 245.60,
            "META": 485.90,
            "AMZN": 178.40,
        }
        return mock_prices.get(ticker.upper(), 100.0)

    def calculate_position_size(
        self,
        signal: Signal,
        max_dollars: Optional[float] = None,
    ) -> int:
        """
        Calculate position size using modified Kelly criterion.

        Args:
            signal: Trading signal
            max_dollars: Max $ for this position

        Returns:
            Number of shares to trade
        """
        max_dollars = max_dollars or (self.max_portfolio * self.max_position_pct)
        price = self._get_price(signal.ticker)

        # Kelly fraction based on confidence
        # f* = (p * b - q) / b
        # where p = win prob, q = lose prob, b = win/loss ratio
        p = signal.confidence
        q = 1 - p
        b = 2.0  # Assume 2:1 win/loss ratio target

        kelly_fraction = (p * b - q) / b
        kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%

        # Calculate dollar amount
        position_value = max_dollars * kelly_fraction

        # Convert to shares
        shares = int(position_value / price)

        return max(1, shares)  # At least 1 share

    def execute_signal(
        self,
        signal: Signal,
        max_position: Optional[float] = None,
    ) -> TradeResult:
        """
        Execute trade based on signal.

        Args:
            signal: Trading signal from SignalGenerator
            max_position: Max $ to allocate

        Returns:
            TradeResult with order details
        """
        if signal.signal_type == SignalType.NEUTRAL:
            return TradeResult(
                order=None,
                success=False,
                message="Neutral signal - no trade",
            )

        # Determine side
        side = OrderSide.BUY if signal.signal_type == SignalType.BULLISH else OrderSide.SELL

        # Check for existing position
        existing = self._positions.get(signal.ticker)

        # If bearish but no position, skip (no shorting in paper mode)
        if side == OrderSide.SELL and not existing:
            if self.mode == "paper":
                return TradeResult(
                    order=None,
                    success=False,
                    message="Cannot short in paper mode",
                )

        # Calculate size
        quantity = self.calculate_position_size(signal, max_position)
        price = self._get_price(signal.ticker)

        # Create order
        order = Order(
            order_id=self._generate_order_id(),
            ticker=signal.ticker,
            side=side,
            quantity=quantity,
            signal_id=f"{signal.ticker}-{signal.generated_at}",
        )

        # Execute (paper or live)
        if self.mode == "paper":
            result = self._execute_paper(order, price)
        else:
            result = self._execute_live(order)

        # Record trade
        self._trade_history.append({
            "timestamp": datetime.now().isoformat(),
            "ticker": signal.ticker,
            "side": side.value,
            "quantity": quantity,
            "price": order.filled_price,
            "signal_confidence": signal.confidence,
            "signal_rationale": signal.rationale[:200],
        })

        return result

    def _execute_paper(self, order: Order, price: float) -> TradeResult:
        """Execute paper trade"""
        order.status = OrderStatus.FILLED
        order.filled_price = price
        order.filled_at = datetime.now().isoformat()

        # Update positions
        ticker = order.ticker
        if order.side == OrderSide.BUY:
            if ticker in self._positions:
                pos = self._positions[ticker]
                # Average up
                total_cost = pos.avg_cost * pos.quantity + price * order.quantity
                total_qty = pos.quantity + order.quantity
                pos.avg_cost = total_cost / total_qty
                pos.quantity = total_qty
            else:
                self._positions[ticker] = Position(
                    ticker=ticker,
                    quantity=order.quantity,
                    avg_cost=price,
                    current_price=price,
                    unrealized_pnl=0,
                    pnl_percent=0,
                )
        else:  # SELL
            if ticker in self._positions:
                pos = self._positions[ticker]
                # Realize P&L
                pnl = (price - pos.avg_cost) * min(order.quantity, pos.quantity)
                self._realized_pnl += pnl

                pos.quantity -= order.quantity
                if pos.quantity <= 0:
                    del self._positions[ticker]

        self._orders.append(order)

        return TradeResult(
            order=order,
            success=True,
            message=f"Paper {order.side.value} {order.quantity} {ticker} @ ${price:.2f}",
        )

    def _execute_live(self, order: Order) -> TradeResult:
        """Execute live trade via broker API"""
        # TODO: Implement Alpaca/IBKR integration
        return TradeResult(
            order=order,
            success=False,
            message="Live trading not yet implemented",
        )

    def get_portfolio(self) -> dict:
        """Get current portfolio state"""
        # Update prices and P&L
        total_value = 0
        for ticker, pos in self._positions.items():
            pos.current_price = self._get_price(ticker)
            pos.unrealized_pnl = (pos.current_price - pos.avg_cost) * pos.quantity
            pos.pnl_percent = (pos.current_price / pos.avg_cost - 1) * 100
            total_value += pos.current_price * pos.quantity

        unrealized = sum(p.unrealized_pnl for p in self._positions.values())

        return {
            "positions": {t: {
                "quantity": p.quantity,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "unrealized_pnl": p.unrealized_pnl,
                "pnl_percent": p.pnl_percent,
            } for t, p in self._positions.items()},
            "total_value": total_value,
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": self._realized_pnl + unrealized,
            "trade_count": len(self._orders),
        }

    def get_trade_history(self) -> list[dict]:
        """Get trade history"""
        return self._trade_history

    def export_report(self, filepath: str):
        """Export trading report to JSON"""
        report = {
            "generated_at": datetime.now().isoformat(),
            "mode": self.mode,
            "portfolio": self.get_portfolio(),
            "trades": self.get_trade_history(),
        }
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)
