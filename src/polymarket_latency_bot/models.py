from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from time import time_ns
from typing import Any


def now_ms() -> int:
    return time_ns() // 1_000_000


class Direction(StrEnum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"


@dataclass(slots=True)
class Prediction:
    source: str
    probability_up: float
    confidence: float
    timestamp_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BookTop:
    token_id: str
    best_bid: float | None = None
    best_ask: float | None = None
    timestamp_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TradeIntent:
    direction: Direction
    token_id: str
    expected_probability: float
    market_price: float
    edge: float
    confidence: float
    notional_usd: float
    created_ms: int
    source_count: int

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        return d
