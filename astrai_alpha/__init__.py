"""
Astrai Alpha - AI-Native Hedge Fund Engine
Built on Astrai infrastructure for 90% cost reduction.

Components:
- SEC filing ingestor (10-K, 8-K, earnings calls)
- Sentiment drift detector
- Signal generator
- Execution cost model (realistic slippage/spread/impact)
- Immutable trade ledger (proof engine)
- Alpaca paper trading gateway (real fills, real latency)
- Risk gate (position limits, daily loss limits)
"""

from .signals import SignalGenerator
from .trader import AlphaTrader
from .execution import ExecutionSimulator, MarketState, ExecutionResult
from .ledger import TradeLedger, LedgerEntry
from .alpaca_gateway import AlpacaGateway, RiskLimits, RiskCheckResult

__all__ = [
    "SignalGenerator",
    "AlphaTrader",
    "ExecutionSimulator",
    "MarketState",
    "ExecutionResult",
    "TradeLedger",
    "LedgerEntry",
    "AlpacaGateway",
    "RiskLimits",
    "RiskCheckResult",
]
