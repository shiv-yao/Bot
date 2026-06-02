from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import now_ms


@dataclass(slots=True)
class PaperMicroOrder:
    order_id: str
    direction: str
    entry_price: float
    notional_usd: float
    shares: float
    probability_up: float
    confidence: float
    created_ms: int
    outcome: str | None = None
    won: bool | None = None
    pnl: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RoundPrediction:
    slug: str
    interval_start_ms: int
    interval_end_ms: int
    question: str
    direction: str = "WAIT"
    status: str = "collecting"
    reason: str = "waiting_for_signal"
    probability_up: float = 0.5
    confidence: float = 0.0
    btc_open: float | None = None
    btc_close: float | None = None
    yes_ask: float | None = None
    no_ask: float | None = None
    entry_price: float | None = None
    notional_usd: float = 0.0
    shares: float = 0.0
    outcome: str | None = None
    won: bool | None = None
    pnl: float = 0.0
    created_ms: int | None = None
    settled_ms: int | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    order_count: int = 0
    total_notional_usd: float = 0.0
    last_order_ms: int | None = None
    last_direction: str | None = None
    last_direction_change_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BTC5mRoundPredictionEngine:
    """Capped Paper high-frequency prediction orders for BTC 5-minute markets.

    This remains a Paper-only simulation. Multiple micro orders may be created
    during one market window, subject to strict per-second, per-round and
    notional caps. Every order settles independently when the BTC five-minute
    round closes.
    """

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        self.settings = settings
        self.state = state
        self.db_path = db_path or "/data/btc5m_prediction_market.db"
        self.lock = asyncio.Lock()
        self.rounds: dict[str, RoundPrediction] = {}
        self.last_reason = "starting"
        self.recent_order_ms: deque[int] = deque()
        self.max_orders_per_sec = max(1, int(os.getenv("BTC5M_PAPER_MAX_ORDERS_PER_SEC", "2")))
        self.max_orders_per_round = max(1, int(os.getenv("BTC5M_PAPER_MAX_ORDERS_PER_ROUND", "20")))
        self.max_round_notional_usd = max(0.01, float(os.getenv("BTC5M_PAPER_MAX_ROUND_NOTIONAL_USD", "25")))
        self.order_notional_usd = max(0.01, float(os.getenv("BTC5M_PAPER_ORDER_USD", "2.5")))
        self.same_direction_cooldown_ms = max(0, int(os.getenv("BTC5M_PAPER_SAME_DIRECTION_COOLDOWN_MS", "500")))
        self.reverse_direction_cooldown_ms = max(0, int(os.getenv("BTC5M_PAPER_REVERSE_DIRECTION_COOLDOWN_MS", "3000")))
        self.close_buffer_sec = max(0, int(os.getenv("BTC5M_PAPER_CLOSE_BUFFER_SEC", "15")))
        self.min_confidence = float(os.getenv("BTC5M_PAPER_MIN_CONFIDENCE", "0.58"))
        self.min_probability_margin = float(os.getenv("BTC5M_PAPER_MIN_PROBABILITY_MARGIN", "0.015"))
        self._init_db()
        self._load_recent()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS btc5m_round_predictions (
                    slug TEXT PRIMARY KEY,
                    interval_start_ms INTEGER NOT NULL,
                    interval_end_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    outcome TEXT,
                    won INTEGER,
                    pnl REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_ms INTEGER NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_btc5m_round_end ON btc5m_round_predictions(interval_end_ms)"
            )

    def _save(self, item: RoundPrediction) -> None:
        payload = json.dumps(item.to_dict(), separators=(",", ":"), ensure_ascii=False)
        won = None if item.won is None else (1 if item.won else 0)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO btc5m_round_predictions (
                    slug, interval_start_ms, interval_end_ms, status, direction,
                    outcome, won, pnl, payload_json, updated_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    status=excluded.status,
                    direction=excluded.direction,
                    outcome=excluded.outcome,
                    won=excluded.won,
                    pnl=excluded.pnl,
                    payload_json=excluded.payload_json,
                    updated_ms=excluded.updated_ms
                """,
                (
                    item.slug,
                    item.interval_start_ms,
                    item.interval_end_ms,
                    item.status,
                    item.direction,
                    item.outcome,
                    won,
                    item.pnl,
                    payload,
                    now_ms(),
                ),
            )

    def _load_recent(self) -> None:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM btc5m_round_predictions ORDER BY interval_start_ms DESC LIMIT 500"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                item = RoundPrediction(**payload)
                if not item.orders and item.entry_price and item.notional_usd and item.created_ms:
                    legacy = PaperMicroOrder(
                        order_id=f"{item.slug}-legacy",
                        direction=item.direction,
                        entry_price=float(item.entry_price),
                        notional_usd=float(item.notional_usd),
                        shares=float(item.shares),
                        probability_up=float(item.probability_up),
                        confidence=float(item.confidence),
                        created_ms=int(item.created_ms),
                        outcome=item.outcome,
                        won=item.won,
                        pnl=float(item.pnl),
                    )
                    item.orders = [legacy.to_dict()]
                    item.order_count = 1
                    item.total_notional_usd = float(item.notional_usd)
                self.rounds[item.slug] = item
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

    async def _btc_prices(self) -> list[tuple[int, float]]:
        async with self.state.lock:
            return list(self.state.btc_prices)

    @staticmethod
    def _first_price_at_or_after(prices: list[tuple[int, float]], target_ms: int) -> float | None:
        return next((float(price) for timestamp, price in prices if timestamp >= target_ms), None)

    @staticmethod
    def _latest_price(prices: list[tuple[int, float]]) -> tuple[int, float] | None:
        if not prices:
            return None
        timestamp, price = prices[-1]
        return int(timestamp), float(price)

    @staticmethod
    def _selected_prediction(snapshot: dict[str, Any]) -> dict[str, Any]:
        predictions = snapshot.get("predictions", {}) or {}
        fusion = snapshot.get("fusion_snapshot", {}) or {}
        return predictions.get("multi_source_fusion") or predictions.get("rtds_momentum_fallback") or fusion

    def _prune_rate_window(self, timestamp: int) -> None:
        while self.recent_order_ms and timestamp - self.recent_order_ms[0] >= 1000:
            self.recent_order_ms.popleft()

    def _can_place_order(self, item: RoundPrediction, direction: str, timestamp: int) -> tuple[bool, str]:
        self._prune_rate_window(timestamp)
        if len(self.recent_order_ms) >= self.max_orders_per_sec:
            return False, "global_rate_limit"
        if item.order_count >= self.max_orders_per_round:
            return False, "round_order_limit"
        if item.total_notional_usd >= self.max_round_notional_usd - 1e-9:
            return False, "round_notional_limit"
        if item.last_order_ms is not None:
            elapsed = timestamp - item.last_order_ms
            if item.last_direction == direction and elapsed < self.same_direction_cooldown_ms:
                return False, "same_direction_cooldown"
            if item.last_direction != direction and elapsed < self.reverse_direction_cooldown_ms:
                return False, "reverse_direction_cooldown"
        return True, "ready"

    async def evaluate(self) -> None:
        snapshot = await self.state.snapshot()
        market = snapshot.get("current_market") or {}
        if snapshot.get("market_discovery_status") != "ready" or not market.get("slug"):
            self.last_reason = "waiting_for_market"
            await self.publish_state()
            return

        slug = str(market["slug"])
        start_ms = int(market.get("interval_start") or 0) * 1000
        end_ms = start_ms + int(market.get("interval_sec") or 300) * 1000
        timestamp = now_ms()
        prices = await self._btc_prices()
        latest = self._latest_price(prices)
        if latest is None:
            self.last_reason = "waiting_for_btc_price"
            await self.publish_state()
            return

        async with self.lock:
            item = self.rounds.get(slug)
            if item is None:
                item = RoundPrediction(
                    slug=slug,
                    interval_start_ms=start_ms,
                    interval_end_ms=end_ms,
                    question=str(market.get("question") or ""),
                )
                self.rounds[slug] = item
            if item.btc_open is None:
                item.btc_open = self._first_price_at_or_after(prices, start_ms) or latest[1]
            self._save(item)

        await self.settle_due_rounds(prices)

        async with self.lock:
            item = self.rounds[slug]
            if item.status in {"settled", "skipped"}:
                self.last_reason = item.reason
                await self.publish_state_locked()
                return

            open_buffer_ms = int(getattr(self.settings, "paper_open_buffer_sec", 2)) * 1000
            close_buffer_ms = self.close_buffer_sec * 1000
            if timestamp < start_ms + open_buffer_ms:
                item.reason = "market_open_buffer"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return
            if timestamp >= end_ms - close_buffer_ms:
                item.reason = "signal_window_closed"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return

            selected = self._selected_prediction(snapshot)
            probability_up = float(selected.get("probability_up") or 0.5)
            confidence = float(selected.get("confidence") or 0.0)
            item.probability_up = probability_up
            item.confidence = confidence

            if confidence < self.min_confidence:
                item.reason = "confidence_too_low"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return

            if probability_up >= 0.5 + self.min_probability_margin:
                direction = "YES"
            elif probability_up <= 0.5 - self.min_probability_margin:
                direction = "NO"
            else:
                item.reason = "direction_margin_too_low"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return

            books = snapshot.get("books", {}) or {}
            yes_book = books.get(str(market.get("yes_token_id") or ""), {}) or {}
            no_book = books.get(str(market.get("no_token_id") or ""), {}) or {}
            item.yes_ask = yes_book.get("best_ask")
            item.no_ask = no_book.get("best_ask")
            entry_price = item.yes_ask if direction == "YES" else item.no_ask
            if entry_price is None or not 0 < float(entry_price) < 1:
                item.reason = "contract_price_missing"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return

            allowed, reason = self._can_place_order(item, direction, timestamp)
            if not allowed:
                item.reason = reason
                self.last_reason = reason
                self._save(item)
                await self.publish_state_locked()
                return

            remaining = max(0.0, self.max_round_notional_usd - item.total_notional_usd)
            notional = min(self.order_notional_usd, remaining)
            if notional <= 0:
                item.reason = "round_notional_limit"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return

            order = PaperMicroOrder(
                order_id=f"{slug}-{item.order_count + 1}-{timestamp}",
                direction=direction,
                entry_price=float(entry_price),
                notional_usd=round(notional, 8),
                shares=round(notional / float(entry_price), 8),
                probability_up=probability_up,
                confidence=confidence,
                created_ms=timestamp,
            )
            if item.last_direction and item.last_direction != direction:
                item.last_direction_change_ms = timestamp
            item.orders.append(order.to_dict())
            item.order_count += 1
            item.total_notional_usd = round(item.total_notional_usd + notional, 8)
            item.last_order_ms = timestamp
            item.last_direction = direction
            item.direction = direction
            item.status = "predicted"
            item.reason = "paper_micro_order_placed"
            item.entry_price = float(entry_price)
            item.notional_usd = item.total_notional_usd
            item.shares = round(sum(float(entry.get("shares") or 0.0) for entry in item.orders), 8)
            item.created_ms = item.created_ms or timestamp
            self.recent_order_ms.append(timestamp)
            self.last_reason = item.reason
            self._save(item)

        async with self.state.lock:
            self.state.orders_submitted += 1
            self.state.last_order_result = {
                "mode": "btc_5m_prediction_market_paper_hf",
                "accepted": True,
                "slug": slug,
                "direction": order.direction,
                "entry_price": order.entry_price,
                "notional_usd": order.notional_usd,
                "round_order_count": item.order_count,
                "round_total_notional_usd": item.total_notional_usd,
            }
        await self.publish_state()

    async def settle_due_rounds(self, prices: list[tuple[int, float]] | None = None) -> None:
        prices = prices if prices is not None else await self._btc_prices()
        latest = self._latest_price(prices)
        if latest is None:
            return
        timestamp, latest_price = latest
        changed = False
        async with self.lock:
            for item in self.rounds.values():
                if item.status not in {"predicted", "collecting"}:
                    continue
                if timestamp < item.interval_end_ms:
                    continue
                close_price = self._first_price_at_or_after(prices, item.interval_end_ms) or latest_price
                item.btc_close = close_price
                if item.btc_open is None:
                    item.btc_open = close_price
                item.outcome = "YES" if close_price > item.btc_open else "NO" if close_price < item.btc_open else "FLAT"
                item.settled_ms = timestamp
                if not item.orders:
                    item.status = "skipped"
                    item.reason = "wait_no_prediction"
                    item.pnl = 0.0
                else:
                    settled_orders: list[dict[str, Any]] = []
                    wins = 0
                    losses = 0
                    total_pnl = 0.0
                    for raw in item.orders:
                        order = PaperMicroOrder(**raw)
                        order.outcome = item.outcome
                        if item.outcome == "FLAT":
                            order.won = None
                            order.pnl = 0.0
                        else:
                            order.won = order.direction == item.outcome
                            if order.won:
                                wins += 1
                                order.pnl = round(order.shares - order.notional_usd, 8)
                            else:
                                losses += 1
                                order.pnl = round(-order.notional_usd, 8)
                        total_pnl += order.pnl
                        settled_orders.append(order.to_dict())
                    item.orders = settled_orders
                    item.status = "settled"
                    item.pnl = round(total_pnl, 8)
                    item.won = wins > losses if wins != losses else None
                    item.reason = "settled_win" if wins > losses else "settled_loss" if losses > wins else "settled_flat"
                self._save(item)
                changed = True
            if changed:
                await self.publish_state_locked()

    def _summary_locked(self) -> dict[str, Any]:
        rounds = list(self.rounds.values())
        settled_rounds = [item for item in rounds if item.status == "settled"]
        open_rounds = [item for item in rounds if item.status == "predicted"]
        skipped = [item for item in rounds if item.status == "skipped"]
        settled_orders = [order for item in settled_rounds for order in item.orders]
        open_orders = [order for item in open_rounds for order in item.orders]
        wins = sum(1 for order in settled_orders if order.get("won") is True)
        losses = sum(1 for order in settled_orders if order.get("won") is False)
        flat = sum(1 for order in settled_orders if order.get("won") is None)
        round_wins = sum(1 for item in settled_rounds if item.won is True)
        round_losses = sum(1 for item in settled_rounds if item.won is False)
        round_flat = sum(1 for item in settled_rounds if item.won is None)
        return {
            "realized_pnl": round(sum(item.pnl for item in settled_rounds), 8),
            "unrealized_pnl": 0.0,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "open_positions": len(open_orders),
            "closed_trades": len(settled_orders),
            "skipped_wait": len(skipped),
            "win_rate": round(wins / max(1, wins + losses), 6),
            "round_wins": round_wins,
            "round_losses": round_losses,
            "round_flat": round_flat,
            "open_rounds": len(open_rounds),
            "closed_rounds": len(settled_rounds),
            "total_orders": len(settled_orders) + len(open_orders),
            "round_win_rate": round(round_wins / max(1, round_wins + round_losses), 6),
        }

    async def publish_state_locked(self) -> None:
        rounds = sorted(self.rounds.values(), key=lambda item: item.interval_start_ms, reverse=True)
        summary = self._summary_locked()
        current = next((item for item in rounds if item.status in {"collecting", "predicted"}), None)
        payload = {
            "summary": summary,
            "current_round": current.to_dict() if current else None,
            "open_positions": [item.to_dict() for item in rounds if item.status == "predicted"],
            "closed_trades": [item.to_dict() for item in rounds if item.status == "settled"][:100],
            "skipped_rounds": [item.to_dict() for item in rounds if item.status == "skipped"][:100],
            "rules": {
                "hft_repeated_orders_enabled": True,
                "paper_only": True,
                "max_orders_per_sec": self.max_orders_per_sec,
                "max_orders_per_round": self.max_orders_per_round,
                "max_round_notional_usd": self.max_round_notional_usd,
                "order_notional_usd": self.order_notional_usd,
                "same_direction_cooldown_ms": self.same_direction_cooldown_ms,
                "reverse_direction_cooldown_ms": self.reverse_direction_cooldown_ms,
                "close_buffer_sec": self.close_buffer_sec,
                "min_confidence": self.min_confidence,
                "min_probability_margin": self.min_probability_margin,
                "settlement": "btc_close_vs_btc_open",
                "wait_excluded_from_win_rate": True,
            },
            "persistence": {"database": self.db_path, "table": "btc5m_round_predictions"},
            "last_reason": self.last_reason,
        }
        async with self.state.lock:
            self.state.paper_portfolio = payload

    async def publish_state(self) -> None:
        async with self.lock:
            await self.publish_state_locked()

    async def loop(self) -> None:
        while True:
            try:
                await self.evaluate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.state.lock:
                    self.state.last_error = f"btc5m_round_prediction: {exc}"
            await asyncio.sleep(0.2)
