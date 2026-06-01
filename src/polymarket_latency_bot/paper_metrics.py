from __future__ import annotations

from typing import Any

from .executor import PaperExecutor
from .models import TradeIntent, now_ms


class MeasuredPaperExecutor(PaperExecutor):
    async def submit(self, intent: TradeIntent) -> bool:
        accepted = await super().submit(intent)
        await self.state.increment_counter("paper_submit_ok" if accepted else "paper_submit_skip")
        async with self.state.lock:
            self.state.runtime_counters["max_queue_depth"] = max(
                self.state.runtime_counters.get("max_queue_depth", 0),
                self.state.queue_depth,
            )
        return accepted

    async def place_order(self, intent: TradeIntent) -> dict[str, Any]:
        started_ms = now_ms()
        await self.state.record_latency("queue_wait_ms", max(0, started_ms - intent.created_ms))
        result = await super().place_order(intent)
        finished_ms = now_ms()
        await self.state.record_latency("execution_ms", max(0, finished_ms - started_ms))
        await self.state.record_latency("signal_to_result_ms", max(0, finished_ms - intent.created_ms))
        await self.state.increment_counter("paper_order_result")
        return result
