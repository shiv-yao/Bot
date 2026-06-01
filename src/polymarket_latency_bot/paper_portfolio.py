from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from .logging_utils import log_event
from .models import TradeIntent, now_ms
from .paper_hardened import PaperRuleConfig, PaperRuleEngine
from .persistence import PaperStore


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
    high_water_price: float
    unrealized_pnl: float = 0.0
    order_count: int = 1

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
    order_count: int = 1

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
        self.rejection_counts: dict[str, int] = {}
        self.rules = PaperRuleEngine(
            PaperRuleConfig(
                take_profit_pct=settings.paper_take_profit_pct,
                stop_loss_pct=settings.paper_stop_loss_pct,
                trailing_stop_pct=settings.paper_trailing_stop_pct,
                open_buffer_sec=settings.paper_open_buffer_sec,
                close_buffer_sec=settings.paper_close_buffer_sec,
                max_trades_per_market=settings.paper_max_trades_per_market,
                max_consecutive_losses_per_market=settings.paper_max_consecutive_losses_per_market,
            )
        )
        self.store = self._open_store(settings.paper_db_path)
        summary = self.store.summary()
        self.total_realized_pnl = float(summary.get("realized_pnl", 0.0))
        self.total_closed_trades = int(summary.get("closed_trades", 0))
        self.wins = int(summary.get("wins", 0))
        self.losses = int(summary.get("losses", 0))
        self.flat = int(summary.get("flat", 0))
        for item in reversed(self.store.recent_trades(settings.recent_trade_limit)):
            self.closed_trades.append(PaperTrade(**item))

    def _open_store(self, configured_path: str) -> PaperStore:
        try:
            return PaperStore(configured_path)
        except OSError as exc:
            fallback = "/tmp/polymarket_paper.db"
            log_event(self.logger, "paper_store_fallback", configured_path=configured_path, fallback=fallback, error=str(exc))
            return PaperStore(fallback)

    def _reject(self, reason: str) -> None:
        self.rejection_counts[reason] = self.rejection_counts.get(reason, 0) + 1

    async def publish(self) -> None:
        async with self.lock:
            await self._publish_locked()

    async def reserve_intent(self, token_or_intent: TradeIntent | str) -> bool:
        token_id = token_or_intent.token_id if isinstance(token_or_intent, TradeIntent) else str(token_or_intent)
        async with self.lock:
            market = self.state.current_market or {}
            allowed, reason = self.rules.market_is_open_for_entries(market, now_ms())
            if not allowed:
                self._reject(reason)
                await self._publish_locked()
                return False
            # Existing token positions may receive additional Paper orders. They are
            # aggregated into one weighted-average position to avoid unbounded objects.
            if token_id in self.pending_tokens and token_id not in self.positions:
                self.skipped_duplicates += 1
                self._reject("duplicate_pending_token")
                await self._publish_locked()
                return False
            if token_id not in self.positions:
                limit = int(self.settings.paper_max_open_positions)
                if limit > 0 and len(self.positions) + len(self.pending_tokens) >= limit:
                    self._reject("paper_max_open_positions")
                    await self._publish_locked()
                    return False
            self.pending_tokens.add(token_id)
            return True

    async def release_pending(self, token_id: str) -> None:
        async with self.lock:
            self.pending_tokens.discard(token_id)

    async def open_position(self, intent: TradeIntent) -> dict[str, Any]:
        async with self.lock:
            self.pending_tokens.discard(intent.token_id)
            price = max(float(intent.market_price), 1e-9)
            market = self.state.current_market or {}
            slug = str(market.get("slug") or "")
            existing = self.positions.get(intent.token_id)
            if existing is not None:
                added_notional = float(intent.notional_usd)
                added_shares = added_notional / price
                existing.notional_usd += added_notional
                existing.shares += added_shares
                existing.entry_price = existing.notional_usd / existing.shares
                existing.last_mark_price = price
                existing.high_water_price = max(existing.high_water_price, price)
                existing.order_count += 1
                self.rules.record_open(slug)
                await self._publish_locked()
                log_event(self.logger, "paper_position_aggregated", position=existing.to_dict())
                return {
                    "accepted": True,
                    "mode": "paper",
                    "aggregated": True,
                    "token_id": existing.token_id,
                    "order_count": existing.order_count,
                    "notional_usd": existing.notional_usd,
                    "entry_price": existing.entry_price,
                    "shares": existing.shares,
                }
            limit = int(self.settings.paper_max_open_positions)
            if limit > 0 and len(self.positions) >= limit:
                self._reject("paper_max_open_positions")
                await self._publish_locked()
                return {"accepted": False, "reason": "paper_max_open_positions"}
            position = PaperPosition(
                token_id=intent.token_id,
                direction=intent.direction.value,
                notional_usd=float(intent.notional_usd),
                entry_price=price,
                shares=float(intent.notional_usd) / price,
                opened_ms=now_ms(),
                market_slug=slug,
                condition_id=str(market.get("condition_id") or ""),
                last_mark_price=price,
                high_water_price=price,
            )
            self.positions[intent.token_id] = position
            self.rules.record_open(slug)
            await self._publish_locked()
            log_event(self.logger, "paper_position_opened", position=position.to_dict())
            return {
                "accepted": True,
                "mode": "paper",
                "token_id": position.token_id,
                "order_count": position.order_count,
                "notional_usd": position.notional_usd,
                "entry_price": position.entry_price,
                "shares": position.shares,
            }

    async def mark_loop(self) -> None:
        await self.publish()
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
            current_tokens = {str(self.settings.yes_token_id or ""), str(self.settings.no_token_id or "")}
            for token_id, position in list(self.positions.items()):
                book = self.state.books.get(token_id)
                mark_price = float(book.best_bid) if book is not None and book.best_bid is not None else position.last_mark_price
                position.last_mark_price = mark_price
                position.high_water_price = max(position.high_water_price, mark_price)
                position.unrealized_pnl = round((mark_price - position.entry_price) * position.shares, 8)
                if token_id not in current_tokens:
                    to_close.append((token_id, mark_price, "market_rotated"))
                    continue
                reason = self.rules.exit_reason(
                    entry_price=position.entry_price,
                    mark_price=mark_price,
                    high_water_price=position.high_water_price,
                    opened_ms=position.opened_ms,
                    now_ms=now,
                    hold_sec=self.settings.paper_hold_sec,
                )
                if reason:
                    to_close.append((token_id, mark_price, reason))
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
                order_count=position.order_count,
            )
            self.closed_trades.append(trade)
            self.closed_trades = self.closed_trades[-self.settings.recent_trade_limit:]
            self.total_realized_pnl = round(self.total_realized_pnl + realized, 8)
            self.total_closed_trades += 1
            if realized > 1e-9:
                self.wins += 1
            elif realized < -1e-9:
                self.losses += 1
            else:
                self.flat += 1
            self.rules.record_close(position.market_slug, realized)
            self.store.record_trade(trade)
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
                "open_order_count": sum(p.order_count for p in self.positions.values()),
                "closed_trades": self.total_closed_trades,
                "skipped_duplicates": self.skipped_duplicates,
                "win_rate": round(self.wins / max(1, self.wins + self.losses), 4),
            },
            "open_positions": [p.to_dict() for p in self.positions.values()],
            "closed_trades": [t.to_dict() for t in reversed(self.closed_trades[-20:])],
            "rejection_counts": dict(sorted(self.rejection_counts.items())),
            "rules": self.rules.snapshot(),
            "persistence": {"db_path": self.store.db_path, "restored_closed_trades": self.total_closed_trades},
        }
        async with self.state.lock:
            self.state.paper_portfolio = payload
