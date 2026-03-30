from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time


@dataclass(slots=True)
class SignalEvent:
    mint: str
    side: str = "BUY"
    score: float = 0.0
    source: str = "unknown"
    confidence: float = 0.0
    strategy: str = "unassigned"
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_legacy_dict(self) -> Dict[str, Any]:
        return {
            "mint": self.mint,
            "side": self.side,
            "score": self.score,
            "source": self.source,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "metadata": dict(self.metadata),
            "ts": self.ts,
        }


@dataclass(slots=True)
class OrderIntent:
    mint: str
    side: str
    size_sol: float
    strategy: str
    signal_score: float
    source: str
    limit_price: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_legacy_dict(self) -> Dict[str, Any]:
        return {
            "mint": self.mint,
            "side": self.side,
            "size_sol": self.size_sol,
            "strategy": self.strategy,
            "signal_score": self.signal_score,
            "source": self.source,
            "limit_price": self.limit_price,
            "metadata": dict(self.metadata),
            "ts": self.ts,
        }


@dataclass(slots=True)
class FillEvent:
    mint: str
    side: str
    size_sol: float
    price: float
    status: str = "FILLED"
    tx_sig: str = ""
    strategy: str = "unassigned"
    source: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_position_dict(self) -> Dict[str, Any]:
        return {
            "token": self.mint,
            "entry_price": self.price,
            "last_price": self.price,
            "peak_price": self.price,
            "pnl_pct": 0.0,
            "amount": self.size_sol,
            "source": self.source,
            "strategy": self.strategy,
            "tx_sig": self.tx_sig,
            "opened_at": self.ts,
        }
