"""Core architecture layer for the upgraded trading system.

This package is additive: it wraps the existing engines instead of replacing
or deleting them, so legacy code can continue to run while new code adopts the
structured pipeline.
"""

from .models import SignalEvent, OrderIntent, FillEvent
from .signal_bus import SignalBus
from .portfolio import PortfolioBook
from .architecture import TradingOrchestrator
