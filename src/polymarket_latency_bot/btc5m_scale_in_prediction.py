from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import now_ms


@dataclass(slots=True)
class ScaleInOrder:
    stage: int
    weight: float
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
class ScaleInRound:
    slug: str
    interval_start_ms: int
    interval_end_ms: int
    question: str
    status: str = "collecting"
    reason: str = "waiting_for_signal"
    direction: str = "WAIT"
    probability_up: float = 0.5
    confidence: float = 0.0
    btc_open: float | None = None
    btc_close: float | None = None
    yes_ask: float | None = None
    no_ask: float | None = None
    outcome: str | None = None
    pnl: float = 0.0
    won: bool | None = None
    settled_ms: int | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    last_order_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BTC5mScaleInPredictionEngine:
    """Paper-only BTC 5m prediction market engine with 50/30/20 scale-in.

    Each five-minute market may create at most three Paper predictions:
      stage 1 -> 50% of round budget
      stage 2 -> 30% of round budget
      stage 3 -> 20% of round budget

    Additional stages require the same direction as the first stage and a
    minimum cooldown. All stages settle together at market close.
    """

    STAGES: tuple[float, ...] = (0.50, 0.30, 0.20)

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        self.settings = settings
        self.state = state
        self.db_path = db_path or "/data/btc5m_scale_in_prediction.db"
        self.lock = asyncio.Lock()
        self.rounds: dict[str, ScaleInRound] = {}
        self.last_reason = "starting"
        self.round_budget_usd = max(0.01, float(os.getenv("BTC5M_SCALE_IN_ROUND_BUDGET_USD", "25")))
        self.stage_cooldown_ms = max(0, int(os.getenv("BTC5M_SCALE_IN_STAGE_COOLDOWN_MS", "1000")))
        self.close_buffer_sec = max(0, int(os.getenv("BTC5M_SCALE_IN_CLOSE_BUFFER_SEC", "15")))
        self.min_confidence = float(os.getenv("BTC5M_SCALE_IN_MIN_CONFIDENCE", "0.58"))
        self.min_probability_margin = float(os.getenv("BTC5M_SCALE_IN_MIN_PROBABILITY_MARGIN", "0.015"))
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
                CREATE TABLE IF NOT EXISTS btc5m_scale_in_rounds (
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
                "CREATE INDEX IF NOT EXISTS idx_btc5m_scale_in_end ON btc5m_scale_in_rounds(interval_end_ms)"
            )

    def _save(self, item: ScaleInRound) -> None:
        payload = json.dumps(item.to_dict(), separators=(",", ":"), ensure_ascii=False)
        won = None if item.won is None else (1 if item.won else 0)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO btc5m_scale_in_rounds (
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
                "SELECT payload_json FROM btc5m_scale_in_rounds ORDER BY interval_start_ms DESC LIMIT 500"
            ).fetchall()
        for row in rows:
            try:
                item = ScaleInRound(**json.loads(row["payload_json"]))
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

    def _direction(self, probability_up: float) -> str:
        if probability_up >= 0.5 + self.min_probability_margin:
            return "YES"
        if probability_up <= 0.5 - self.min_probability_margin:
            return "NO"
        return "WAIT"

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
                item = ScaleInRound(
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
            if len(item.orders) >= len(self.STAGES):
                item.reason = "scale_in_complete"
                self.last_reason = item.reason
                self._save(item)
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
            if item.last_order_ms is not None and timestamp - item.last_order_ms < self.stage_cooldown_ms:
                item.reason = "stage_cooldown"
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

            direction = self._direction(probability_up)
            if direction == "WAIT":
                item.reason = "direction_margin_too_low"
                self.last_reason = item.reason
                self._save(item)
                await self.publish_state_locked()
                return
            if item.orders and item.direction != direction:
                item.reason = "direction_changed_no_scale_in"
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

            stage = len(item.orders) + 1
            weight = self.STAGES[stage - 1]
            notional = round(self.round_budget_usd * weight, 8)
            order = ScaleInOrder(
                stage=stage,
                weight=weight,
                direction=direction,
                entry_price=float(entry_price),
                notional_usd=notional,
                shares=round(notional / float(entry_price), 8),
                probability_up=probability_up,
                confidence=confidence,
                created_ms=timestamp,
            )
            item.orders.append(order.to_dict())
            item.direction = direction
            item.status = "predicted"
            item.reason = f"scale_in_stage_{stage}_placed"
            item.last_order_ms = timestamp
            self.last_reason = item.reason
            self._save(item)

        async with self.state.lock:
            self.state.orders_submitted += 1
            self.state.last_order_result = {
                "mode": "btc_5m_scale_in_prediction_paper",
                "accepted": True,
                "slug": slug,
                "direction": order.direction,
                "stage": order.stage,
                "weight": order.weight,
                "entry_price": order.entry_price,
                "notional_usd": order.notional_usd,
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
                if item.status not in {"collecting", "predicted"}:
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
                    settled: list[dict[str, Any]] = []
                    total_pnl = 0.0
                    for raw in item.orders:
                        order = ScaleInOrder(**raw)
                        order.outcome = item.outcome
                        if item.outcome == "FLAT":
                            order.won = None
                            order.pnl = 0.0
                        else:
                            order.won = order.direction == item.outcome
                            order.pnl = round(order.shares - order.notional_usd, 8) if order.won else round(-order.notional_usd, 8)
                        total_pnl += order.pnl
                        settled.append(order.to_dict())
                    item.orders = settled
                    item.status = "settled"
                    item.pnl = round(total_pnl, 8)
                    item.won = None if item.outcome == "FLAT" else item.direction == item.outcome
                    item.reason = "settled_win" if item.won is True else "settled_loss" if item.won is False else "settled_flat"
                self._save(item)
                changed = True
            if changed:
                await self.publish_state_locked()

    def _summary_locked(self) -> dict[str, Any]:
        rounds = list(self.rounds.values())
        settled_rounds = [item for item in rounds if item.status == "settled"]
        open_rounds = [item for item in rounds if item.status == "predicted"]
        skipped_rounds = [item for item in rounds if item.status == "skipped"]
        settled_orders = [order for item in settled_rounds for order in item.orders]
        open_orders = [order for item in open_rounds for order in item.orders]
        wins = sum(1 for item in settled_rounds if item.won is True)
        losses = sum(1 for item in settled_rounds if item.won is False)
        flat = sum(1 for item in settled_rounds if item.won is None)
        return {
            "realized_pnl": round(sum(item.pnl for item in settled_rounds), 8),
            "unrealized_pnl": 0.0,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "win_rate": round(wins / max(1, wins + losses), 6),
            "open_positions": len(open_orders),
            "open_rounds": len(open_rounds),
            "closed_trades": len(settled_rounds),
            "closed_rounds": len(settled_rounds),
            "skipped_wait": len(skipped_rounds),
            "total_scale_in_orders": len(settled_orders) + len(open_orders),
        }

    async def publish_state_locked(self) -> None:
        rounds = sorted(self.rounds.values(), key=lambda item: item.interval_start_ms, reverse=True)
        payload = {
            "summary": self._summary_locked(),
            "current_round": next((item.to_dict() for item in rounds if item.status in {"collecting", "predicted"}), None),
            "open_positions": [item.to_dict() for item in rounds if item.status == "predicted"],
            "closed_trades": [item.to_dict() for item in rounds if item.status == "settled"][:100],
            "skipped_rounds": [item.to_dict() for item in rounds if item.status == "skipped"][:100],
            "rules": {
                "paper_only": True,
                "scale_in_enabled": True,
                "stage_weights": list(self.STAGES),
                "max_scale_in_orders_per_round": 3,
                "same_direction_required": True,
                "round_budget_usd": self.round_budget_usd,
                "stage_cooldown_ms": self.stage_cooldown_ms,
                "close_buffer_sec": self.close_buffer_sec,
                "min_confidence": self.min_confidence,
                "min_probability_margin": self.min_probability_margin,
                "settlement": "btc_close_vs_btc_open",
                "wait_excluded_from_win_rate": True,
            },
            "persistence": {"database": self.db_path, "table": "btc5m_scale_in_rounds"},
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
                    self.state.last_error = f"btc5m_scale_in_prediction: {exc}"
            await asyncio.sleep(0.5)
