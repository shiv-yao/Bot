from __future__ import annotations

import asyncio
import json
import logging
from time import time
from typing import Any, Awaitable, Callable

import aiohttp
import websockets

from .config import Settings
from .logging_utils import log_event
from .models import BookTop, Prediction, now_ms
from .state import BotState

OnChange = Callable[[], Awaitable[None]]


def _json_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not part:
            continue
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return decoded
    raise ValueError("expected a JSON list")


def _normalize_levels(raw_levels: list[Any], *, reverse: bool, limit: int) -> list[dict[str, float]]:
    levels: list[dict[str, float]] = []
    for raw in raw_levels:
        try:
            if isinstance(raw, dict):
                price = float(raw["price"])
                size = float(raw.get("size") or raw.get("amount") or 0)
            else:
                price = float(raw[0])
                size = float(raw[1])
            if price > 0 and size > 0:
                levels.append({"price": price, "size": size})
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    levels.sort(key=lambda item: item["price"], reverse=reverse)
    return levels[:limit]


def _update_level(levels: list[dict[str, float]], *, price: float, size: float, reverse: bool, limit: int) -> list[dict[str, float]]:
    updated = [dict(level) for level in levels if float(level["price"]) != price]
    if size > 0 and price > 0:
        updated.append({"price": price, "size": size})
    updated.sort(key=lambda item: item["price"], reverse=reverse)
    return updated[:limit]


class FeedHub:
    def __init__(self, settings: Settings, state: BotState, on_change: OnChange) -> None:
        self.settings = settings
        self.state = state
        self.on_change = on_change
        self.logger = logging.getLogger("feeds")

    async def upsert_prediction(self, prediction: Prediction) -> None:
        if not 0 <= prediction.probability_up <= 1:
            raise ValueError("probability_up must be between 0 and 1")
        if not 0 <= prediction.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        async with self.state.lock:
            self.state.predictions[prediction.source] = prediction
        log_event(self.logger, "prediction_update", **prediction.to_dict())
        await self.on_change()

    async def market_discovery_loop(self) -> None:
        if not self.settings.auto_discover_market:
            async with self.state.lock:
                self.state.market_discovery_status = "disabled"
            return
        timeout = aiohttp.ClientTimeout(total=self.settings.market_discovery_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    discovered = await self._discover_current_market(session)
                    if discovered is not None:
                        changed = (
                            discovered["yes_token_id"] != self.settings.yes_token_id
                            or discovered["no_token_id"] != self.settings.no_token_id
                            or discovered["condition_id"] != self.settings.condition_id
                        )
                        self.settings.yes_token_id = discovered["yes_token_id"]
                        self.settings.no_token_id = discovered["no_token_id"]
                        self.settings.condition_id = discovered["condition_id"]
                        async with self.state.lock:
                            self.state.current_market = discovered
                            self.state.market_discovery_status = "ready"
                            self.state.last_market_discovery_ms = now_ms()
                            if changed:
                                self.state.books.clear()
                                self.state.ws_market_connected = False
                        if changed:
                            log_event(self.logger, "market_discovered", **discovered)
                    else:
                        async with self.state.lock:
                            self.state.market_discovery_status = "waiting_for_market"
                            self.state.last_market_discovery_ms = now_ms()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    async with self.state.lock:
                        self.state.market_discovery_status = "error"
                        self.state.last_market_discovery_ms = now_ms()
                        self.state.last_error = f"market_discovery: {exc}"
                    log_event(self.logger, "market_discovery_error", error=str(exc))
                await asyncio.sleep(self.settings.market_discovery_refresh_sec)

    async def _discover_current_market(self, session: aiohttp.ClientSession) -> dict[str, Any] | None:
        interval = self.settings.market_interval_sec
        current = int(time() // interval * interval)
        for timestamp in (current, current - interval):
            slug = f"{self.settings.market_slug_prefix}{timestamp}"
            async with session.get(f"{self.settings.gamma_api_url.rstrip('/')}/markets", params={"slug": slug}) as response:
                response.raise_for_status()
                payload = await response.json()
            if not payload:
                continue
            market = payload[0] if isinstance(payload, list) else payload
            token_ids = _decode_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
            outcomes = _decode_list(market.get("outcomes", ["Up", "Down"]))
            if len(token_ids) < 2:
                continue
            return {
                "slug": slug,
                "market_id": str(market.get("id", "")),
                "condition_id": str(market.get("conditionId") or market.get("condition_id") or ""),
                "yes_token_id": str(token_ids[0]),
                "no_token_id": str(token_ids[1]),
                "outcomes": [str(x) for x in outcomes],
                "interval_start": timestamp,
                "interval_sec": interval,
                "question": str(market.get("question", "")),
                "active": bool(market.get("active", True)),
                "closed": bool(market.get("closed", False)),
            }
        return None

    async def market_ws_loop(self) -> None:
        while True:
            if not self.settings.yes_token_id or not self.settings.no_token_id:
                await asyncio.sleep(1)
                continue
            subscribed_ids = [self.settings.yes_token_id, self.settings.no_token_id]
            subscribe = {"assets_ids": subscribed_ids, "type": "market", "custom_feature_enabled": True}
            try:
                async with websockets.connect(self.settings.market_ws_url, ping_interval=10, ping_timeout=10) as ws:
                    async with self.state.lock:
                        self.state.ws_market_connected = True
                    await ws.send(json.dumps(subscribe))
                    log_event(self.logger, "market_ws_connected", assets_ids=subscribed_ids)
                    while True:
                        if subscribed_ids != [self.settings.yes_token_id, self.settings.no_token_id]:
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        await self._handle_market_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.state.lock:
                    self.state.last_error = f"market_ws: {exc}"
                log_event(self.logger, "market_ws_error", error=str(exc))
                await asyncio.sleep(1)
            finally:
                async with self.state.lock:
                    self.state.ws_market_connected = False

    async def _handle_market_message(self, raw: str | bytes) -> None:
        message = json.loads(raw)
        messages = message if isinstance(message, list) else [message]
        changed = False
        async with self.state.lock:
            for item in messages:
                if not isinstance(item, dict):
                    continue
                event = item.get("event_type") or item.get("type")
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                if event == "book" and token_id:
                    bids = _normalize_levels(item.get("bids") or [], reverse=True, limit=self.settings.depth_levels)
                    asks = _normalize_levels(item.get("asks") or [], reverse=False, limit=self.settings.depth_levels)
                    self.state.books[token_id] = BookTop(
                        token_id=token_id,
                        best_bid=bids[0]["price"] if bids else None,
                        best_ask=asks[0]["price"] if asks else None,
                        timestamp_ms=now_ms(),
                        bid_levels=bids,
                        ask_levels=asks,
                    )
                    changed = True
                elif event == "best_bid_ask" and token_id:
                    current = self.state.books.get(token_id, BookTop(token_id))
                    if item.get("best_bid") is not None:
                        current.best_bid = float(item["best_bid"])
                    if item.get("best_ask") is not None:
                        current.best_ask = float(item["best_ask"])
                    current.timestamp_ms = now_ms()
                    self.state.books[token_id] = current
                    changed = True
                elif event == "price_change":
                    for change in item.get("price_changes") or []:
                        asset_id = str(change.get("asset_id") or "")
                        if not asset_id:
                            continue
                        current = self.state.books.get(asset_id, BookTop(asset_id))
                        if change.get("best_bid") is not None:
                            current.best_bid = float(change["best_bid"])
                        if change.get("best_ask") is not None:
                            current.best_ask = float(change["best_ask"])
                        try:
                            level_price = float(change.get("price"))
                            level_size = float(change.get("size"))
                            side = str(change.get("side") or "").upper()
                            if side == "BUY":
                                current.bid_levels = _update_level(current.bid_levels, price=level_price, size=level_size, reverse=True, limit=self.settings.depth_levels)
                            elif side == "SELL":
                                current.ask_levels = _update_level(current.ask_levels, price=level_price, size=level_size, reverse=False, limit=self.settings.depth_levels)
                        except (TypeError, ValueError):
                            pass
                        current.timestamp_ms = now_ms()
                        self.state.books[asset_id] = current
                        changed = True
        if changed:
            await self.on_change()

    def _build_rtds_momentum_prediction(self, prices: list[tuple[int, float]]) -> Prediction | None:
        if len(prices) < 2:
            return None
        latest_ts, latest_price = prices[-1]
        target_ts = latest_ts - self.settings.rtds_prediction_window_sec * 1000
        reference = next((price for ts, price in prices if ts >= target_ts), None)
        if reference is None or reference <= 0 or latest_price <= 0:
            return None
        momentum = latest_price / reference - 1
        return Prediction(
            source="rtds_momentum_fallback",
            probability_up=max(0.30, min(0.70, 0.50 + momentum * 40)),
            confidence=max(0.55, min(0.75, 0.55 + abs(momentum) * 50)),
            timestamp_ms=latest_ts,
        )

    async def user_ws_loop(self) -> None:
        if not self.settings.live_enabled:
            log_event(self.logger, "user_ws_disabled", reason="paper mode")
            return
        log_event(self.logger, "user_ws_disabled", reason="live user websocket intentionally disabled in Paper branch")

    async def external_poll_loop(self) -> None:
        if not self.settings.external_poll_url:
            log_event(self.logger, "external_poll_disabled")
            return
        headers = {"Authorization": f"Bearer {self.settings.external_poll_api_key}"} if self.settings.external_poll_api_key else {}
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                try:
                    async with session.get(self.settings.external_poll_url, timeout=3) as response:
                        response.raise_for_status()
                        payload = await response.json()
                    await self.upsert_prediction(Prediction(
                        source=self.settings.external_poll_source,
                        probability_up=float(_json_path(payload, self.settings.external_probability_json_path)),
                        confidence=float(_json_path(payload, self.settings.external_confidence_json_path)),
                        timestamp_ms=now_ms(),
                    ))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    async with self.state.lock:
                        self.state.last_error = f"external_poll: {exc}"
                    log_event(self.logger, "external_poll_error", error=str(exc))
                await asyncio.sleep(self.settings.external_poll_interval_sec)
