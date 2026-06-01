from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import Settings
from .models import TradeIntent, now_ms


@dataclass(slots=True)
class RiskSnapshot:
    day_start_equity: float
    realized_pnl: float = 0.0
    open_notional: float = 0.0
    halted: bool = False
    halt_reason: str = ""


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = asyncio.Lock()
        self.snapshot = RiskSnapshot(day_start_equity=settings.account_equity_usd)

    async def check(self, intent: TradeIntent) -> tuple[bool, str]:
        async with self.lock:
            max_order = self.settings.account_equity_usd * self.settings.max_order_equity_fraction
            max_loss = self.snapshot.day_start_equity * self.settings.max_daily_loss_fraction
            if self.snapshot.realized_pnl <= -max_loss:
                self.snapshot.halted = True
                self.snapshot.halt_reason = "daily_loss_limit"
            if self.snapshot.halted:
                return False, self.snapshot.halt_reason
            if now_ms() - intent.created_ms > self.settings.max_signal_age_ms:
                return False, "stale_signal"
            if intent.notional_usd > max_order + 1e-9:
                return False, "max_single_order"
            if self.snapshot.open_notional + intent.notional_usd > self.settings.max_open_notional_usd:
                return False, "max_open_notional"
            self.snapshot.open_notional += intent.notional_usd
            return True, "ok"

    async def record_result(self, reserved_notional: float, realized_pnl_delta: float = 0.0) -> None:
        async with self.lock:
            self.snapshot.open_notional = max(0.0, self.snapshot.open_notional - reserved_notional)
            self.snapshot.realized_pnl += realized_pnl_delta

    async def manual_pnl_adjustment(self, delta: float) -> RiskSnapshot:
        async with self.lock:
            self.snapshot.realized_pnl += delta
            max_loss = self.snapshot.day_start_equity * self.settings.max_daily_loss_fraction
            if self.snapshot.realized_pnl <= -max_loss:
                self.snapshot.halted = True
                self.snapshot.halt_reason = "daily_loss_limit"
            return self.snapshot
