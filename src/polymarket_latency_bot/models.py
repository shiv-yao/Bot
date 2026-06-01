from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    bid_levels: list[dict[str, float]] = field(default_factory=list)
    ask_levels: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return max(0.0, float(self.best_ask) - float(self.best_bid))

    def ask_depth_usd(self, levels: int = 5) -> float:
        return round(sum(float(level["price"]) * float(level["size"]) for level in self.ask_levels[:levels]), 8)

    def bid_depth_usd(self, levels: int = 5) -> float:
        return round(sum(float(level["price"]) * float(level["size"]) for level in self.bid_levels[:levels]), 8)

    def estimate_buy_vwap(self, notional_usd: float, levels: int = 5) -> float | None:
        remaining = float(notional_usd)
        spent = 0.0
        shares = 0.0
        for level in self.ask_levels[:levels]:
            price = float(level["price"])
            size = float(level["size"])
            available_usd = price * size
            take_usd = min(remaining, available_usd)
            if take_usd <= 0 or price <= 0:
                continue
            spent += take_usd
            shares += take_usd / price
            remaining -= take_usd
            if remaining <= 1e-9:
                break
        if remaining > 1e-9 or shares <= 0:
            return None
        return spent / shares


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
    spread: float = 0.0
    estimated_vwap: float | None = None
    slippage: float = 0.0
    ask_depth_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["direction"] = self.direction.value
        return data
