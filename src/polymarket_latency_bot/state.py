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
        self.external_prices: dict[str, list[tuple[int, float]]] = {
            "chainlink": [], "binance": [], "coinbase": [],
        }
        self.source_status: dict[str, dict[str, Any]] = {
            "chainlink": {"connected": False},
            "binance": {"connected": False},
            "coinbase": {"connected": False},
        }
        self.fusion_snapshot: dict[str, Any] = {
            "status": "waiting_for_sources", "source_count": 0, "required_sources": 2,
        }
        self.strategy_rejections: dict[str, int] = {}
        self.last_strategy_snapshot: dict[str, Any] = {}
        self.current_market: dict[str, Any] | None = None
        self.market_discovery_status = "pending"
        self.last_market_discovery_ms: int | None = None
        self.last_intent: TradeIntent | None = None
        self.last_order_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.orders_submitted = 0
        self.orders_rejected = 0
        self.queue_depth = 0
        self.ws_market_connected = False
        self.ws_user_connected = False
        self.ws_rtds_connected = False
        self.paper_portfolio: dict[str, Any] = {
            "summary": {
                "realized_pnl": 0.0, "unrealized_pnl": 0.0, "wins": 0, "losses": 0,
                "flat": 0, "open_positions": 0, "closed_trades": 0, "skipped_duplicates": 0,
            },
            "open_positions": [], "closed_trades": [], "rejection_counts": {}, "rules": {},
        }
        self.started_ms = now_ms()

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            timestamp = now_ms()
            source_status = {}
            for source, status in self.source_status.items():
                item = dict(status)
                last_update = item.get("last_update_ms")
                if isinstance(last_update, int):
                    item["age_ms"] = max(0, timestamp - last_update)
                source_status[source] = item
            return {
                "uptime_ms": timestamp - self.started_ms,
                "current_market": self.current_market,
                "market_discovery_status": self.market_discovery_status,
                "last_market_discovery_ms": self.last_market_discovery_ms,
                "books": {k: v.to_dict() for k, v in self.books.items()},
                "predictions": {k: v.to_dict() for k, v in self.predictions.items()},
                "btc_prices_tail": list(self.btc_prices)[-10:],
                "external_prices": self.external_prices,
                "source_status": source_status,
                "fusion_snapshot": self.fusion_snapshot,
                "strategy_rejections": self.strategy_rejections,
                "last_strategy_snapshot": self.last_strategy_snapshot,
                "paper_portfolio": self.paper_portfolio,
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
