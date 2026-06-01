from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from .models import BookTop, Prediction, TradeIntent, now_ms


class BotState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.books: dict[str, BookTop] = {}
        self.predictions: dict[str, Prediction] = {}
        self.btc_prices: deque[tuple[int, float]] = deque(maxlen=512)
        self.last_intent: TradeIntent | None = None
        self.last_order_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.orders_submitted = 0
        self.orders_rejected = 0
        self.queue_depth = 0
        self.ws_market_connected = False
        self.ws_user_connected = False
        self.ws_rtds_connected = False
        self.started_ms = now_ms()

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return {
                "uptime_ms": now_ms() - self.started_ms,
                "books": {k: v.to_dict() for k, v in self.books.items()},
                "predictions": {k: v.to_dict() for k, v in self.predictions.items()},
                "btc_prices_tail": list(self.btc_prices)[-10:],
                "last_intent": self.last_intent.to_dict() if self.last_intent else None,
                "last_order_result": self.last_order_result,
                "last_error": self.last_error,
                "orders_submitted": self.orders_submitted,
                "orders_rejected": self.orders_rejected,
                "queue_depth": self.queue_depth,
                "connections": {
                    "market_ws": self.ws_market_connected,
                    "user_ws": self.ws_user_connected,
                    "rtds_ws": self.ws_rtds_connected,
                },
            }
