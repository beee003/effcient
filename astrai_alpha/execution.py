"""
Execution Cost Model - Renaissance-style realistic simulation

This is where alpha lives or dies. If your edge disappears
after conservative costs, it's not real.

Components:
1. Bid-ask spread (varies by liquidity)
2. Market impact (function of size + ADV)
3. Latency simulation
4. Partial fill probability
5. Rejection modeling
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Liquidity(Enum):
    """Liquidity tier affects all cost components"""
    MEGA_CAP = "mega_cap"      # AAPL, MSFT, NVDA - pennies spread
    LARGE_CAP = "large_cap"    # S&P 500 stocks
    MID_CAP = "mid_cap"        # Russell 1000
    SMALL_CAP = "small_cap"    # Russell 2000
    MICRO_CAP = "micro_cap"    # Below $300M market cap


@dataclass
class ExecutionParams:
    """Parameters for execution simulation"""
    # Spread in basis points by liquidity tier
    spread_bps: dict[Liquidity, float] = None

    # Market impact coefficient (Almgren-Chriss style)
    # Impact = coefficient * sqrt(participation_rate) * volatility
    impact_coefficient: float = 0.1

    # Latency in milliseconds (mean, std)
    latency_ms_mean: float = 50.0
    latency_ms_std: float = 20.0

    # Partial fill probability by urgency
    partial_fill_prob: float = 0.15

    # Rejection probability (broker/exchange rejects)
    rejection_prob: float = 0.02

    # Fixed costs per trade
    commission_per_share: float = 0.0035  # IBKR tiered
    sec_fee_per_dollar: float = 0.0000278  # SEC fee

    def __post_init__(self):
        if self.spread_bps is None:
            # Default spreads in basis points
            self.spread_bps = {
                Liquidity.MEGA_CAP: 1.0,     # 0.01%
                Liquidity.LARGE_CAP: 3.0,    # 0.03%
                Liquidity.MID_CAP: 8.0,      # 0.08%
                Liquidity.SMALL_CAP: 20.0,   # 0.20%
                Liquidity.MICRO_CAP: 50.0,   # 0.50%
            }


@dataclass
class MarketState:
    """Current market conditions for a security"""
    ticker: str
    mid_price: float
    bid: float
    ask: float
    spread_bps: float
    daily_volume: int           # Average daily volume
    volatility_annual: float    # Annualized volatility
    liquidity: Liquidity

    @classmethod
    def from_ticker(cls, ticker: str, mid_price: float) -> "MarketState":
        """Create market state with reasonable defaults for known tickers"""
        # Known liquid tickers
        mega_caps = {"AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK.B"}
        large_caps = {"JPM", "V", "JNJ", "WMT", "PG", "MA", "HD", "DIS"}

        ticker = ticker.upper()

        if ticker in mega_caps:
            liquidity = Liquidity.MEGA_CAP
            spread_bps = 1.0
            daily_volume = 50_000_000
            volatility = 0.35
        elif ticker in large_caps:
            liquidity = Liquidity.LARGE_CAP
            spread_bps = 3.0
            daily_volume = 10_000_000
            volatility = 0.25
        else:
            # Default to mid-cap
            liquidity = Liquidity.MID_CAP
            spread_bps = 8.0
            daily_volume = 2_000_000
            volatility = 0.40

        spread_dollars = mid_price * spread_bps / 10000

        return cls(
            ticker=ticker,
            mid_price=mid_price,
            bid=mid_price - spread_dollars / 2,
            ask=mid_price + spread_dollars / 2,
            spread_bps=spread_bps,
            daily_volume=daily_volume,
            volatility_annual=volatility,
            liquidity=liquidity,
        )


@dataclass
class ExecutionResult:
    """Result of simulated execution"""
    # What we wanted
    intended_price: float
    intended_quantity: int

    # What we got
    filled_price: float
    filled_quantity: int

    # Cost breakdown (all in dollars)
    spread_cost: float
    impact_cost: float
    commission: float
    sec_fee: float
    total_cost: float

    # Cost as basis points of notional
    cost_bps: float

    # Timing
    latency_ms: float

    # Status
    fully_filled: bool
    rejected: bool
    rejection_reason: Optional[str] = None

    # Slippage from mid (what you lost vs perfect execution)
    slippage_bps: float = 0.0


class ExecutionSimulator:
    """
    Realistic execution simulation.

    This is the difference between fantasy and reality.
    Every trade goes through this before hitting the ledger.

    Usage:
        sim = ExecutionSimulator()
        market = MarketState.from_ticker("NVDA", 875.50)
        result = sim.execute(
            market=market,
            side="buy",
            quantity=100,
            urgency=0.5,
        )
        print(f"Filled {result.filled_quantity} @ {result.filled_price}")
        print(f"Total cost: {result.cost_bps:.1f} bps")
    """

    def __init__(self, params: Optional[ExecutionParams] = None, seed: Optional[int] = None):
        self.params = params or ExecutionParams()
        if seed:
            random.seed(seed)

    def execute(
        self,
        market: MarketState,
        side: str,  # "buy" or "sell"
        quantity: int,
        urgency: float = 0.5,  # 0 = patient, 1 = aggressive
        limit_price: Optional[float] = None,
    ) -> ExecutionResult:
        """
        Simulate trade execution with realistic costs.

        Args:
            market: Current market state
            side: "buy" or "sell"
            quantity: Number of shares
            urgency: 0-1, affects market impact
            limit_price: Optional limit (None = market order)

        Returns:
            ExecutionResult with all cost components
        """
        # Check for rejection first
        if random.random() < self.params.rejection_prob:
            return ExecutionResult(
                intended_price=market.mid_price,
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
                rejection_reason="Order rejected by exchange",
            )

        # Simulate latency
        latency = max(1, random.gauss(
            self.params.latency_ms_mean,
            self.params.latency_ms_std
        ))

        # Calculate participation rate (how much of daily volume we're taking)
        participation_rate = quantity / market.daily_volume

        # Market impact (Almgren-Chriss square root model)
        # Impact increases with urgency and participation
        impact_bps = (
            self.params.impact_coefficient
            * math.sqrt(participation_rate)
            * market.volatility_annual
            * urgency
            * 10000  # Convert to bps
        )

        # Spread cost (half spread for crossing)
        spread_bps = market.spread_bps / 2

        # Determine execution price
        if side == "buy":
            # Start at ask, add impact
            base_price = market.ask
            impact_dollars = market.mid_price * impact_bps / 10000
            execution_price = base_price + impact_dollars
        else:
            # Start at bid, subtract impact
            base_price = market.bid
            impact_dollars = market.mid_price * impact_bps / 10000
            execution_price = base_price - impact_dollars

        # Check limit price
        if limit_price:
            if side == "buy" and execution_price > limit_price:
                # Would cross limit - partial fill or reject
                if random.random() < 0.5:
                    quantity = int(quantity * random.uniform(0.1, 0.5))
                    execution_price = limit_price
                else:
                    return ExecutionResult(
                        intended_price=market.mid_price,
                        intended_quantity=quantity,
                        filled_price=0,
                        filled_quantity=0,
                        spread_cost=0,
                        impact_cost=0,
                        commission=0,
                        sec_fee=0,
                        total_cost=0,
                        cost_bps=0,
                        latency_ms=latency,
                        fully_filled=False,
                        rejected=True,
                        rejection_reason="Limit price exceeded",
                    )
            elif side == "sell" and execution_price < limit_price:
                if random.random() < 0.5:
                    quantity = int(quantity * random.uniform(0.1, 0.5))
                    execution_price = limit_price
                else:
                    return ExecutionResult(
                        intended_price=market.mid_price,
                        intended_quantity=quantity,
                        filled_price=0,
                        filled_quantity=0,
                        spread_cost=0,
                        impact_cost=0,
                        commission=0,
                        sec_fee=0,
                        total_cost=0,
                        cost_bps=0,
                        latency_ms=latency,
                        fully_filled=False,
                        rejected=True,
                        rejection_reason="Limit price not met",
                    )

        # Partial fill simulation
        filled_quantity = quantity
        if random.random() < self.params.partial_fill_prob:
            fill_rate = random.uniform(0.3, 0.9)
            filled_quantity = max(1, int(quantity * fill_rate))

        # Calculate costs
        notional = execution_price * filled_quantity

        spread_cost = market.mid_price * spread_bps / 10000 * filled_quantity
        impact_cost = market.mid_price * impact_bps / 10000 * filled_quantity
        commission = self.params.commission_per_share * filled_quantity
        sec_fee = self.params.sec_fee_per_dollar * notional if side == "sell" else 0

        total_cost = spread_cost + impact_cost + commission + sec_fee
        cost_bps = (total_cost / notional) * 10000 if notional > 0 else 0

        # Slippage from mid
        slippage = abs(execution_price - market.mid_price) / market.mid_price * 10000

        return ExecutionResult(
            intended_price=market.mid_price,
            intended_quantity=quantity,
            filled_price=execution_price,
            filled_quantity=filled_quantity,
            spread_cost=spread_cost,
            impact_cost=impact_cost,
            commission=commission,
            sec_fee=sec_fee,
            total_cost=total_cost,
            cost_bps=cost_bps,
            latency_ms=latency,
            fully_filled=(filled_quantity == quantity),
            rejected=False,
            slippage_bps=slippage,
        )

    def estimate_cost(
        self,
        ticker: str,
        price: float,
        quantity: int,
        urgency: float = 0.5,
    ) -> float:
        """
        Quick cost estimate without simulation randomness.
        Returns expected cost in basis points.
        """
        market = MarketState.from_ticker(ticker, price)
        participation = quantity / market.daily_volume

        # Spread (half)
        spread_bps = market.spread_bps / 2

        # Impact
        impact_bps = (
            self.params.impact_coefficient
            * math.sqrt(participation)
            * market.volatility_annual
            * urgency
            * 10000
        )

        # Commission
        commission_bps = (self.params.commission_per_share / price) * 10000

        return spread_bps + impact_bps + commission_bps


def demonstrate_cost_reality():
    """
    Show why costs matter.

    If you have a 51% win rate with 1:1 payoff,
    but costs are 10 bps per trade,
    you need 20 bps of alpha just to break even.
    """
    sim = ExecutionSimulator(seed=42)

    print("=" * 60)
    print("EXECUTION COST REALITY CHECK")
    print("=" * 60)

    scenarios = [
        ("NVDA", 875.50, 100, "Small retail"),
        ("NVDA", 875.50, 1000, "Active trader"),
        ("NVDA", 875.50, 10000, "Small fund"),
        ("AAPL", 182.30, 5000, "Diversified"),
    ]

    for ticker, price, qty, label in scenarios:
        market = MarketState.from_ticker(ticker, price)
        result = sim.execute(market, "buy", qty, urgency=0.5)

        print(f"\n{label}: {qty} shares of {ticker} @ ${price:.2f}")
        print(f"  Notional:     ${qty * price:,.0f}")
        print(f"  Spread cost:  ${result.spread_cost:.2f} ({market.spread_bps/2:.1f} bps)")
        print(f"  Impact cost:  ${result.impact_cost:.2f}")
        print(f"  Commission:   ${result.commission:.2f}")
        print(f"  TOTAL COST:   ${result.total_cost:.2f} ({result.cost_bps:.1f} bps)")

        # What edge do you need to overcome this?
        # Round trip = 2x cost
        round_trip_bps = result.cost_bps * 2
        print(f"  Round-trip:   {round_trip_bps:.1f} bps (your minimum edge)")


if __name__ == "__main__":
    demonstrate_cost_reality()
