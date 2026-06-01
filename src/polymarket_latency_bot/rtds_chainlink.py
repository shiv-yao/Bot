from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from .logging_utils import log_event
from .models import now_ms


async def chainlink_rtds_loop(settings: Any, state: Any, feeds: Any, fusion: Any | None = None) -> None:
    """Maintain Chainlink BTC/USD RTDS, fallback prediction, and optional fusion input."""
    logger = logging.getLogger("rtds_chainlink")
    subscribe = {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":")),
            }
        ],
    }

    while True:
        try:
            async with websockets.connect(settings.rtds_ws_url, ping_interval=5, ping_timeout=10) as ws:
                async with state.lock:
                    state.ws_rtds_connected = True
                    state.source_status["chainlink"] = {"connected": True}
                await ws.send(json.dumps(subscribe))
                log_event(logger, "rtds_chainlink_connected", symbol="btc/usd")
                async for raw in ws:
                    await _handle_message(raw, state, feeds, logger, fusion)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with state.lock:
                state.last_error = f"rtds_chainlink: {exc}"
                state.source_status["chainlink"] = {"connected": False, "error": str(exc)}
            log_event(logger, "rtds_chainlink_error", error=str(exc))
            await asyncio.sleep(1)
        finally:
            async with state.lock:
                state.ws_rtds_connected = False


async def _handle_message(raw: str | bytes, state: Any, feeds: Any, logger: logging.Logger, fusion: Any | None = None) -> None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        return

    if item.get("topic") != "crypto_prices_chainlink":
        return
    payload = item.get("payload") or {}
    symbol = str(payload.get("symbol") or "").lower()
    if symbol and symbol != "btc/usd":
        return
    value = payload.get("value")
    if value is None:
        return

    price = float(value)
    timestamp = now_ms()
    async with state.lock:
        state.btc_prices.append((timestamp, price))
        prices = list(state.btc_prices)
        state.source_status["chainlink"] = {
            "connected": True,
            "last_price": price,
            "last_update_ms": timestamp,
            "age_ms": 0,
        }

    log_event(logger, "rtds_chainlink_price", symbol="btc/usd", price=price)
    if fusion is not None:
        await fusion.record_price("chainlink", price, timestamp)
    if feeds.settings.enable_rtds_momentum_prediction:
        prediction = feeds._build_rtds_momentum_prediction(prices)
        if prediction is not None:
            await feeds.upsert_prediction(prediction)
