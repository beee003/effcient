"""
Astrai Alpha - AI-Native Hedge Fund Engine
Built on Astrai infrastructure for 90% cost reduction.

Components:
- SEC filing ingestor (10-K, 8-K, earnings calls)
- Sentiment drift detector
- Signal generator
- Paper/live trading executor
"""

from .signals import SignalGenerator
from .trader import AlphaTrader

__all__ = ["SignalGenerator", "AlphaTrader"]
