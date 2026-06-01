from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from .config import Settings
from .logging_utils import log_event
from .models import TradeIntent, now_ms
from .risk import RiskManager
from .state import BotState


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = rate_per_sec
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.updated = monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = monotonic()
                elapsed = now - self.updated
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = max(0.001, (1 - self.tokens) / self.rate)
            await asyncio.sleep(delay)


class BaseExecutor:
    def __init__(self, settings: Settings, state: BotState, risk: RiskManager) -> None:
        self.settings = settings
        self.state = state
        self.risk = risk
        self.queue: asyncio.Queue[TradeIntent] = asyncio.Queue(maxsize=settings.max_queue_size)
        self.bucket = TokenBucket(settings.order_rate_per_sec, settings.order_burst)
        self.logger = logging.getLogger("executor")

    async def submit(self, intent: TradeIntent) -> None:
        try:
            self.queue.put_nowait(intent)
            async with self.state.lock:
                self.state.queue_depth = self.queue.qsize()
                self.state.last_intent = intent
        except asyncio.QueueFull:
            async with self.state.lock:
                self.state.orders_rejected += 1
                self.state.last_error = "execution queue full"
            log_event(self.logger, "intent_dropped", reason="queue_full", intent=intent.to_dict())

    async def worker(self, worker_id: int) -> None:
        while True:
            intent = await self.queue.get()
            async with self.state.lock:
                self.state.queue_depth = self.queue.qsize()
            try:
                approved, reason = await self.risk.check(intent)
                if not approved:
                    async with self.state.lock:
                        self.state.orders_rejected += 1
                    log_event(self.logger, "risk_reject", worker_id=worker_id, reason=reason, intent=intent.to_dict())
                    continue
                await self.bucket.acquire()
                started = now_ms()
                result = await asyncio.wait_for(
                    self.place_order(intent),
                    timeout=self.settings.order_timeout_ms / 1000,
                )
                latency = now_ms() - started
                async with self.state.lock:
                    self.state.orders_submitted += 1
                    self.state.last_order_result = {"latency_ms": latency, "result": result}
                log_event(self.logger, "order_result", worker_id=worker_id, latency_ms=latency, result=result)
            except asyncio.TimeoutError:
                async with self.state.lock:
                    self.state.orders_rejected += 1
                    self.state.last_error = "order timeout"
                log_event(self.logger, "order_timeout", worker_id=worker_id)
            except Exception as exc:
                async with self.state.lock:
                    self.state.orders_rejected += 1
                    self.state.last_error = f"order error: {exc}"
                log_event(self.logger, "order_error", worker_id=worker_id, error=str(exc))
            finally:
                await self.risk.record_result(intent.notional_usd)
                self.queue.task_done()

    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        raise NotImplementedError


class PaperExecutor(BaseExecutor):
    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {
            "mode": "paper",
            "accepted": True,
            "token_id": intent.token_id,
            "notional_usd": intent.notional_usd,
            "market_price": intent.market_price,
            "edge": intent.edge,
        }


class LiveExecutor(BaseExecutor):
    def __init__(self, settings: Settings, state: BotState, risk: RiskManager) -> None:
        super().__init__(settings, state, risk)
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from py_clob_client_v2 import ApiCreds, ClobClient
        creds = None
        if all([self.settings.clob_api_key, self.settings.clob_secret, self.settings.clob_pass_phrase]):
            creds = ApiCreds(
                api_key=self.settings.clob_api_key,
                api_secret=self.settings.clob_secret,
                api_passphrase=self.settings.clob_pass_phrase,
            )
        if creds is None:
            temp = ClobClient(
                host=self.settings.clob_host,
                chain_id=self.settings.chain_id,
                key=self.settings.pk,
            )
            creds = temp.create_or_derive_api_key()
        self._client = ClobClient(
            host=self.settings.clob_host,
            chain_id=self.settings.chain_id,
            key=self.settings.pk,
            creds=creds,
            signature_type=self.settings.signature_type,
            funder=self.settings.funder_address,
        )
        return self._client

    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        return await asyncio.to_thread(self._place_sync, intent)

    def _place_sync(self, intent: TradeIntent) -> dict[str, Any]:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side
        client = self._get_client()
        response = client.create_and_post_market_order(
            order_args=MarketOrderArgs(
                token_id=intent.token_id,
                amount=intent.notional_usd,
                side=Side.BUY,
                order_type=OrderType.FAK,
            ),
            options=PartialCreateOrderOptions(
                tick_size=self.settings.tick_size,
                neg_risk=self.settings.neg_risk,
            ),
            order_type=OrderType.FAK,
        )
        if isinstance(response, dict):
            return response
        return {"raw": str(response)}
