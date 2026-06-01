from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PaperRuleConfig:
    take_profit_pct: float = 0.08
    stop_loss_pct: float = 0.04
    trailing_stop_pct: float = 0.03
    open_buffer_sec: int = 30
    close_buffer_sec: int = 90
    # Set to 0 to disable the per-market trade-count cap.
    max_trades_per_market: int = 0
    max_consecutive_losses_per_market: int = 2


class PaperRuleEngine:
    def __init__(self, config: PaperRuleConfig) -> None:
        self.config = config
        self.market_trade_counts: dict[str, int] = {}
        self.market_consecutive_losses: dict[str, int] = {}
        self.paused_markets: set[str] = set()

    def market_is_open_for_entries(self, market: dict[str, Any] | None, now_ms: int) -> tuple[bool, str]:
        if not market:
            return False, "market_missing"
        slug = str(market.get("slug") or "")
        interval_start = int(market.get("interval_start") or 0)
        if not slug or interval_start <= 0:
            return False, "market_metadata_missing"
        elapsed = now_ms / 1000 - interval_start
        interval_sec = int(market.get("interval_sec") or 900)
        if elapsed < self.config.open_buffer_sec:
            return False, "market_open_buffer"
        if elapsed > interval_sec - self.config.close_buffer_sec:
            return False, "market_close_buffer"
        if slug in self.paused_markets:
            return False, "market_paused_after_losses"
        if self.config.max_trades_per_market > 0 and self.market_trade_counts.get(slug, 0) >= self.config.max_trades_per_market:
            return False, "market_trade_limit"
        return True, "ok"

    def record_open(self, slug: str) -> None:
        self.market_trade_counts[slug] = self.market_trade_counts.get(slug, 0) + 1

    def record_close(self, slug: str, realized_pnl: float) -> None:
        if realized_pnl < 0:
            losses = self.market_consecutive_losses.get(slug, 0) + 1
            self.market_consecutive_losses[slug] = losses
            if losses >= self.config.max_consecutive_losses_per_market:
                self.paused_markets.add(slug)
        elif realized_pnl > 0:
            self.market_consecutive_losses[slug] = 0

    def exit_reason(self, *, entry_price: float, mark_price: float, high_water_price: float, opened_ms: int, now_ms: int, hold_sec: int) -> str | None:
        if entry_price <= 0:
            return "invalid_entry_price"
        pnl_pct = mark_price / entry_price - 1
        if pnl_pct >= self.config.take_profit_pct:
            return "take_profit"
        if pnl_pct <= -self.config.stop_loss_pct:
            return "stop_loss"
        if high_water_price > entry_price and mark_price <= high_water_price * (1 - self.config.trailing_stop_pct):
            return "trailing_stop"
        if now_ms - opened_ms >= hold_sec * 1000:
            return "time_stop"
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "market_trade_counts": dict(self.market_trade_counts),
            "market_consecutive_losses": dict(self.market_consecutive_losses),
            "paused_markets": sorted(self.paused_markets),
            "config": {
                "take_profit_pct": self.config.take_profit_pct,
                "stop_loss_pct": self.config.stop_loss_pct,
                "trailing_stop_pct": self.config.trailing_stop_pct,
                "open_buffer_sec": self.config.open_buffer_sec,
                "close_buffer_sec": self.config.close_buffer_sec,
                "max_trades_per_market": self.config.max_trades_per_market,
                "max_consecutive_losses_per_market": self.config.max_consecutive_losses_per_market,
            },
        }
