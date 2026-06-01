from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from .logging_utils import log_event
from .models import TradeIntent, now_ms


@dataclass(slots=True)
class PaperPosition:
    token_id: str
    direction: str
    notional_usd: float
    entry_price: float
    shares: float
    opened_ms: int
    market_slug: str
    condition_id: str
    last_mark_price: float
    unrealized_pnl: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PaperTrade:
    token_id: str
    direction: str
    notional_usd: float
    entry_price: float
    exit_price: float
    shares: float
    realized_pnl: float
    opened_ms: int
    closed_ms: int
    hold_ms: int
    close_reason: str
    market_slug: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperPortfolio:
    def __init__(self, settings: Any, state: Any, risk: Any, logger: Any) -> None:
        self.settings = settings
        self.state = state
        self.risk = risk
        self.logger = logger
        self.lock = asyncio.Lock()
        self.pending_tokens: set[str] = set()
        self.positions: dict[str, PaperPosition] = {}
        self.closed_trades: list[PaperTrade] = []
        self.skipped_duplicates = 0
        self.wins = 0
        self.losses = 0
        self.flat = 0
        self.total_realized_pnl = 0.0
        self.total_closed_trades = 0

    async def reserve_intent(self, token_id: str) -> bool:
        async with self.lock:
            if token_id in self.pending_tokens or token_id in self.positions:
                self.skipped_duplicates += 1
                await self._publish_locked()
                return False
            if len(self.positions) + len(self.pending_tokens) >= self.settings.paper_max_open_positions:
                return False
            self.pending_tokens.add(token_id)
            return True

    async def release_pending(self, token_id: str) -> None:
        async with self.lock:
            self.pending_tokens.discard(token_id)

    async def open_position(self, intent: TradeIntent) -> dict[str, Any]:
        async with self.lock:
            self.pending_tokens.discard(intent.token_id)
            if intent.token_id in self.positions:
                self.skipped_duplicates += 1
                await self._publish_locked()
                return {"accepted": False, "reason": "duplicate_open_position"}
            if len(self.positions) >= self.settings.paper_max_open_positions:
                return {"accepted": False, "reason": "paper_max_open_positions"}
            price = max(float(intent.market_price), 1e-9)
            market = self.state.current_market or {}
            position = PaperPosition(
                token_id=intent.token_id,
                direction=intent.direction.value,
                notional_usd=float(intent.notional_usd),
                entry_price=price,
                shares=float(intent.notional_usd) / price,
                opened_ms=now_ms(),
                market_slug=str(market.get("slug") or ""),
                condition_id=str(market.get("condition_id") or ""),
                last_mark_price=price,
            )
            self.positions[intent.token_id] = position
            await self._publish_locked()
            log_event(self.logger, "paper_position_opened", position=position.to_dict())
            return {
                "accepted": True,
                "mode": "paper",
                "token_id": position.token_id,
                "notional_usd": position.notional_usd,
                "entry_price": position.entry_price,
                "shares": position.shares,
            }

    async def mark_loop(self) -> None:
        while True:
            try:
                await self.mark_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(self.logger, "paper_mark_error", error=str(exc))
            await asyncio.sleep(self.settings.paper_mark_interval_sec)

    async def mark_once(self) -> None:
        now = now_ms()
        to_close: list[tuple[str, float, str]] = []
        async with self.lock:
            current_tokens = {
                str(self.settings.yes_token_id or ""),
                str(self.settings.no_token_id or ""),
            }
            for token_id, position in list(self.positions.items()):
                book = self.state.books.get(token_id)
                mark_price = (
                    float(book.best_bid)
                    if book is not None and book.best_bid is not None
                    else position.last_mark_price
                )
                position.last_mark_price = mark_price
                position.unrealized_pnl = round((mark_price - position.entry_price) * position.shares, 8)
                if token_id not in current_tokens:
                    to_close.append((token_id, mark_price, "market_rotated"))
                elif now - position.opened_ms >= self.settings.paper_hold_sec * 1000:
                    to_close.append((token_id, mark_price, "hold_time_elapsed"))
            await self._publish_locked()
        for token_id, price, reason in to_close:
            await self.close_position(token_id, price, reason)

    async def close_position(self, token_id: str, exit_price: float, reason: str) -> None:
        async with self.lock:
            position = self.positions.pop(token_id, None)
            if position is None:
                return
            closed_ms = now_ms()
            realized = round((float(exit_price) - position.entry_price) * position.shares, 8)
            trade = PaperTrade(
                token_id=position.token_id,
                direction=position.direction,
                notional_usd=position.notional_usd,
                entry_price=position.entry_price,
                exit_price=float(exit_price),
                shares=position.shares,
                realized_pnl=realized,
                opened_ms=position.opened_ms,
                closed_ms=closed_ms,
                hold_ms=closed_ms - position.opened_ms,
                close_reason=reason,
                market_slug=position.market_slug,
            )
            self.closed_trades.append(trade)
            self.closed_trades = self.closed_trades[-100:]
            self.total_realized_pnl = round(self.total_realized_pnl + realized, 8)
            self.total_closed_trades += 1
            if realized > 1e-9:
                self.wins += 1
            elif realized < -1e-9:
                self.losses += 1
            else:
                self.flat += 1
            await self._publish_locked()
        await self.risk.record_result(position.notional_usd, realized)
        log_event(self.logger, "paper_position_closed", trade=trade.to_dict())

    async def _publish_locked(self) -> None:
        unrealized = round(sum(p.unrealized_pnl for p in self.positions.values()), 8)
        payload = {
            "summary": {
                "realized_pnl": self.total_realized_pnl,
                "unrealized_pnl": unrealized,
                "net_pnl": round(self.total_realized_pnl + unrealized, 8),
                "wins": self.wins,
                "losses": self.losses,
                "flat": self.flat,
                "open_positions": len(self.positions),
                "closed_trades": self.total_closed_trades,
                "skipped_duplicates": self.skipped_duplicates,
                "win_rate": round(self.wins / max(1, self.wins + self.losses), 4),
            },
            "open_positions": [p.to_dict() for p in self.positions.values()],
            "closed_trades": [t.to_dict() for t in reversed(self.closed_trades[-20:])],
        }
        async with self.state.lock:
            self.state.paper_portfolio = payload
