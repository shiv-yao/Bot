from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from .config import Settings
from .logging_utils import log_event
from .models import TradeIntent, now_ms
from .paper_portfolio import PaperPortfolio
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

    @property
    def retain_risk_reservation_after_success(self) -> bool:
        return False

    @property
    def rate_limit_enabled(self) -> bool:
        return True

    async def submit(self, intent: TradeIntent) -> bool:
        try:
            self.queue.put_nowait(intent)
            async with self.state.lock:
                self.state.queue_depth = self.queue.qsize()
                self.state.last_intent = intent
            return True
        except asyncio.QueueFull:
            async with self.state.lock:
                self.state.orders_rejected += 1
                self.state.last_error = "execution queue full"
            await self.on_failed_intent(intent)
            log_event(self.logger, "intent_dropped", reason="queue_full", intent=intent.to_dict())
            return False

    async def on_failed_intent(self, intent: TradeIntent) -> None:
        return None

    async def worker(self, worker_id: int) -> None:
        while True:
            intent = await self.queue.get()
            reserved = False
            try:
                async with self.state.lock:
                    self.state.queue_depth = self.queue.qsize()
                approved, reason = await self.risk.check(intent)
                if not approved:
                    async with self.state.lock:
                        self.state.orders_rejected += 1
                    await self.on_failed_intent(intent)
                    log_event(self.logger, "risk_reject", worker_id=worker_id, reason=reason, intent=intent.to_dict())
                    continue
                reserved = True
                if self.rate_limit_enabled:
                    await self.bucket.acquire()
                started = now_ms()
                result = await asyncio.wait_for(
                    self.place_order(intent),
                    timeout=self.settings.order_timeout_ms / 1000,
                )
                latency = now_ms() - started
                accepted = bool(result.get("accepted", True)) if isinstance(result, dict) else True
                async with self.state.lock:
                    self.state.last_order_result = {"latency_ms": latency, "result": result}
                    if accepted:
                        self.state.orders_submitted += 1
                    else:
                        self.state.orders_rejected += 1
                if accepted:
                    if not self.retain_risk_reservation_after_success:
                        await self.risk.record_result(intent.notional_usd)
                        reserved = False
                    log_event(self.logger, "order_result", worker_id=worker_id, latency_ms=latency, result=result)
                else:
                    await self.risk.record_result(intent.notional_usd)
                    reserved = False
                    await self.on_failed_intent(intent)
                    log_event(self.logger, "order_rejected", worker_id=worker_id, latency_ms=latency, result=result)
            except asyncio.TimeoutError:
                if reserved:
                    await self.risk.record_result(intent.notional_usd)
                await self.on_failed_intent(intent)
                async with self.state.lock:
                    self.state.orders_rejected += 1
                    self.state.last_error = "order timeout"
                log_event(self.logger, "order_timeout", worker_id=worker_id)
            except Exception as exc:
                if reserved:
                    await self.risk.record_result(intent.notional_usd)
                await self.on_failed_intent(intent)
                async with self.state.lock:
                    self.state.orders_rejected += 1
                    self.state.last_error = f"order error: {exc}"
                log_event(self.logger, "order_error", worker_id=worker_id, error=str(exc))
            finally:
                self.queue.task_done()

    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        raise NotImplementedError


class PaperExecutor(BaseExecutor):
    def __init__(
        self,
        settings: Settings,
        state: BotState,
        risk: RiskManager,
        portfolio: PaperPortfolio,
    ) -> None:
        super().__init__(settings, state, risk)
        self.portfolio = portfolio

    @property
    def retain_risk_reservation_after_success(self) -> bool:
        return True

    @property
    def rate_limit_enabled(self) -> bool:
        return not self.settings.paper_disable_order_rate_limit

    async def submit(self, intent: TradeIntent) -> bool:
        accepted = await self.portfolio.reserve_intent(intent.token_id)
        if not accepted:
            log_event(self.logger, "paper_intent_skipped", reason="duplicate_or_max_positions", token_id=intent.token_id)
            return False
        queued = await super().submit(intent)
        if not queued:
            await self.portfolio.release_pending(intent.token_id)
        return queued

    async def on_failed_intent(self, intent: TradeIntent) -> None:
        await self.portfolio.release_pending(intent.token_id)

    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        await asyncio.sleep(0)
        return await self.portfolio.open_position(intent)


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
