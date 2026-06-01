from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
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
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


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

    async def market_ws_loop(self) -> None:
        if not self.settings.yes_token_id or not self.settings.no_token_id:
            log_event(self.logger, "market_ws_disabled", reason="YES_TOKEN_ID and NO_TOKEN_ID are required")
            return
        subscribe = {
            "assets_ids": [self.settings.yes_token_id, self.settings.no_token_id],
            "type": "market",
            "custom_feature_enabled": True,
        }
        while True:
            try:
                async with websockets.connect(self.settings.market_ws_url, ping_interval=10, ping_timeout=10) as ws:
                    async with self.state.lock:
                        self.state.ws_market_connected = True
                    await ws.send(json.dumps(subscribe))
                    log_event(self.logger, "market_ws_connected")
                    async for raw in ws:
                        await self._handle_market_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.state.lock:
                    self.state.ws_market_connected = False
                    self.state.last_error = f"market_ws: {exc}"
                log_event(self.logger, "market_ws_error", error=str(exc))
                await asyncio.sleep(1)

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
                    bids = item.get("bids") or []
                    asks = item.get("asks") or []
                    bid = max((float(x["price"]) for x in bids), default=None)
                    ask = min((float(x["price"]) for x in asks), default=None)
                    self.state.books[token_id] = BookTop(token_id, bid, ask, now_ms())
                    changed = True
                elif event in {"best_bid_ask", "price_change"} and token_id:
                    current = self.state.books.get(token_id, BookTop(token_id))
                    if item.get("best_bid") is not None:
                        current.best_bid = float(item["best_bid"])
                    if item.get("best_ask") is not None:
                        current.best_ask = float(item["best_ask"])
                    current.timestamp_ms = now_ms()
                    self.state.books[token_id] = current
                    changed = True
        if changed:
            await self.on_change()

    async def rtds_loop(self) -> None:
        subscribe = {
            "action": "subscribe",
            "subscriptions": [{"topic": "crypto_prices", "type": "update", "filters": "btcusdt"}],
        }
        while True:
            try:
                async with websockets.connect(self.settings.rtds_ws_url, ping_interval=None) as ws:
                    async with self.state.lock:
                        self.state.ws_rtds_connected = True
                    await ws.send(json.dumps(subscribe))
                    log_event(self.logger, "rtds_ws_connected")
                    pinger = asyncio.create_task(self._rtds_ping(ws))
                    try:
                        async for raw in ws:
                            await self._handle_rtds_message(raw)
                    finally:
                        pinger.cancel()
                        with suppress(asyncio.CancelledError):
                            await pinger
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.state.lock:
                    self.state.ws_rtds_connected = False
                    self.state.last_error = f"rtds_ws: {exc}"
                log_event(self.logger, "rtds_ws_error", error=str(exc))
                await asyncio.sleep(1)

    async def _rtds_ping(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(5)
            await ws.send("PING")

    async def _handle_rtds_message(self, raw: str | bytes) -> None:
        if raw == "PONG":
            return
        item = json.loads(raw)
        if item.get("topic") != "crypto_prices":
            return
        payload = item.get("payload") or {}
        symbol = str(payload.get("symbol") or payload.get("s") or "").lower()
        if symbol and symbol != "btcusdt":
            return
        price = payload.get("price") or payload.get("p")
        if price is None:
            return
        async with self.state.lock:
            self.state.btc_prices.append((now_ms(), float(price)))

    async def user_ws_loop(self) -> None:
        if not self.settings.live_enabled:
            log_event(self.logger, "user_ws_disabled", reason="paper mode")
            return
        if not all([self.settings.clob_api_key, self.settings.clob_secret, self.settings.clob_pass_phrase]):
            log_event(self.logger, "user_ws_disabled", reason="missing L2 API credentials")
            return
        subscribe = {
            "auth": {
                "apiKey": self.settings.clob_api_key,
                "secret": self.settings.clob_secret,
                "passphrase": self.settings.clob_pass_phrase,
            },
            "markets": [self.settings.condition_id] if self.settings.condition_id else [],
            "type": "user",
        }
        while True:
            try:
                async with websockets.connect(self.settings.user_ws_url, ping_interval=10, ping_timeout=10) as ws:
                    async with self.state.lock:
                        self.state.ws_user_connected = True
                    await ws.send(json.dumps(subscribe))
                    log_event(self.logger, "user_ws_connected")
                    async for raw in ws:
                        item = json.loads(raw)
                        log_event(self.logger, "user_ws_event", payload=item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.state.lock:
                    self.state.ws_user_connected = False
                    self.state.last_error = f"user_ws: {exc}"
                log_event(self.logger, "user_ws_error", error=str(exc))
                await asyncio.sleep(1)

    async def external_poll_loop(self) -> None:
        if not self.settings.external_poll_url:
            log_event(self.logger, "external_poll_disabled")
            return
        headers = {}
        if self.settings.external_poll_api_key:
            headers["Authorization"] = f"Bearer {self.settings.external_poll_api_key}"
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                try:
                    async with session.get(self.settings.external_poll_url, timeout=3) as response:
                        response.raise_for_status()
                        payload = await response.json()
                    prediction = Prediction(
                        source=self.settings.external_poll_source,
                        probability_up=float(_json_path(payload, self.settings.external_probability_json_path)),
                        confidence=float(_json_path(payload, self.settings.external_confidence_json_path)),
                        timestamp_ms=now_ms(),
                    )
                    await self.upsert_prediction(prediction)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    async with self.state.lock:
                        self.state.last_error = f"external_poll: {exc}"
                    log_event(self.logger, "external_poll_error", error=str(exc))
                await asyncio.sleep(self.settings.external_poll_interval_sec)
