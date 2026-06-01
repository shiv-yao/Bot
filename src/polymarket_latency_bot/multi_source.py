from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

import websockets

from .logging_utils import log_event
from .models import Prediction, now_ms


class MultiSourceFusion:
    def __init__(self, settings: Any, state: Any, feeds: Any) -> None:
        self.settings = settings
        self.state = state
        self.feeds = feeds
        self.logger = logging.getLogger("multi_source")
        self._prices: dict[str, deque[tuple[int, float]]] = {
            "chainlink": deque(maxlen=1024),
            "binance": deque(maxlen=1024),
            "coinbase": deque(maxlen=1024),
        }
        self._weights = {
            "chainlink": float(settings.fusion_source_weight_chainlink),
            "binance": float(settings.fusion_source_weight_binance),
            "coinbase": float(settings.fusion_source_weight_coinbase),
        }

    async def record_price(self, source: str, price: float, timestamp_ms: int | None = None) -> None:
        if source not in self._prices or price <= 0:
            return
        timestamp = int(timestamp_ms or now_ms())
        self._prices[source].append((timestamp, float(price)))
        async with self.state.lock:
            self.state.external_prices[source] = list(self._prices[source])[-20:]
            self.state.source_status[source] = {
                "connected": True,
                "last_price": float(price),
                "last_update_ms": timestamp,
                "age_ms": 0,
            }
        if self.settings.enable_multi_source_fusion:
            await self._publish_fusion(timestamp)

    async def mark_disconnected(self, source: str, error: str) -> None:
        async with self.state.lock:
            current = dict(self.state.source_status.get(source, {}))
            current.update({"connected": False, "error": error})
            self.state.source_status[source] = current

    async def _publish_fusion(self, timestamp: int) -> None:
        window_start = timestamp - self.settings.external_price_window_sec * 1000
        fresh_cutoff = timestamp - self.settings.external_price_max_age_ms
        samples: list[dict[str, float | str]] = []
        for source, prices in self._prices.items():
            if not prices or prices[-1][0] < fresh_cutoff:
                continue
            latest_ts, latest_price = prices[-1]
            reference = next((price for ts, price in prices if ts >= window_start), None)
            if reference is None or reference <= 0:
                continue
            momentum = latest_price / reference - 1
            samples.append({
                "source": source,
                "weight": self._weights[source],
                "momentum": momentum,
                "price": latest_price,
                "timestamp_ms": latest_ts,
            })
        if len(samples) < self.settings.fusion_min_sources:
            async with self.state.lock:
                self.state.fusion_snapshot = {
                    "status": "waiting_for_sources",
                    "source_count": len(samples),
                    "required_sources": self.settings.fusion_min_sources,
                    "samples": samples,
                    "timestamp_ms": timestamp,
                }
            return
        total_weight = sum(float(item["weight"]) for item in samples) or 1.0
        fused_momentum = sum(float(item["momentum"]) * float(item["weight"]) for item in samples) / total_weight
        positive = sum(1 for item in samples if float(item["momentum"]) >= 0)
        negative = len(samples) - positive
        agreement = max(positive, negative) / len(samples)
        probability_up = max(0.30, min(0.70, 0.50 + fused_momentum * self.settings.fusion_probability_scale))
        confidence = max(0.0, min(0.90, self.settings.fusion_base_confidence + abs(fused_momentum) * 80 + max(0.0, agreement - 0.5) * 0.30))
        status = "ready" if agreement >= self.settings.fusion_agreement_threshold else "low_agreement"
        snapshot = {
            "status": status,
            "probability_up": probability_up,
            "confidence": confidence,
            "agreement": agreement,
            "fused_momentum": fused_momentum,
            "source_count": len(samples),
            "samples": samples,
            "timestamp_ms": timestamp,
        }
        async with self.state.lock:
            self.state.fusion_snapshot = snapshot
        if status == "ready":
            await self.feeds.upsert_prediction(Prediction(
                source="multi_source_fusion",
                probability_up=probability_up,
                confidence=confidence,
                timestamp_ms=timestamp,
            ))


async def binance_ws_loop(settings: Any, state: Any, fusion: MultiSourceFusion) -> None:
    logger = logging.getLogger("binance_ws")
    if not settings.enable_binance_ws:
        return
    while True:
        try:
            async with websockets.connect(settings.binance_ws_url, ping_interval=20, ping_timeout=10) as ws:
                log_event(logger, "binance_ws_connected")
                async for raw in ws:
                    item = json.loads(raw)
                    price = item.get("p")
                    if price is not None:
                        await fusion.record_price("binance", float(price))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await fusion.mark_disconnected("binance", str(exc))
            log_event(logger, "binance_ws_error", error=str(exc))
            await asyncio.sleep(1)


async def coinbase_ws_loop(settings: Any, state: Any, fusion: MultiSourceFusion) -> None:
    logger = logging.getLogger("coinbase_ws")
    if not settings.enable_coinbase_ws:
        return
    subscribe = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker"],
    }
    while True:
        try:
            async with websockets.connect(settings.coinbase_ws_url, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps(subscribe))
                log_event(logger, "coinbase_ws_connected")
                async for raw in ws:
                    item = json.loads(raw)
                    if item.get("type") == "ticker" and item.get("product_id") == "BTC-USD" and item.get("price") is not None:
                        await fusion.record_price("coinbase", float(item["price"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await fusion.mark_disconnected("coinbase", str(exc))
            log_event(logger, "coinbase_ws_error", error=str(exc))
            await asyncio.sleep(1)
