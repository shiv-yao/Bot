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
class PaperMicroOrder:
    order_id: str
    direction: str
    entry_price: float
    notional_usd: float
    shares: float
    probability_up: float
    confidence: float
    created_ms: int
    scale_stage: int = 1
    scale_weight: float = 0.5
    expected_probability: float = 0.5
    edge: float = 0.0
    net_edge: float = 0.0
    spread: float = 0.0
    estimated_vwap: float | None = None
    signal_age_ms: int = 0
    book_age_ms: int = 0
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
    initial_direction: str | None = None
    next_scale_stage: int = 1
    rejection_counts: dict[str, int] = field(default_factory=dict)
    last_rejection_key: str | None = None
    last_signal_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BTC5mRoundPredictionEngine:
    """Paper-only BTC five-minute prediction engine with guarded scale-in entries.

    Each round may create at most three simulated entries. The round budget is
    split into 50%, 30% and 20% stages. Every additional stage must pass stricter
    signal, edge, freshness and order-book checks while keeping the first
    direction. The runtime never places live orders.
    """

    DEFAULT_SCALE_WEIGHTS = (0.50, 0.30, 0.20)
    DEFAULT_SCALE_AFTER_SEC = (0, 100, 200)
    DEFAULT_STAGE_MIN_CONFIDENCE = (0.58, 0.62, 0.66)
    DEFAULT_STAGE_MIN_NET_EDGE = (0.008, 0.012, 0.018)

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        self.settings = settings
        self.state = state
        self.db_path = db_path or "/data/btc5m_prediction_market.db"
        self.lock = asyncio.Lock()
        self.rounds: dict[str, RoundPrediction] = {}
        self.last_reason = "starting"
        self.max_round_notional_usd = max(0.01, float(os.getenv("BTC5M_PAPER_MAX_ROUND_NOTIONAL_USD", "25")))
        self.close_buffer_sec = max(0, int(os.getenv("BTC5M_PAPER_CLOSE_BUFFER_SEC", "15")))
        self.min_confidence = float(os.getenv("BTC5M_PAPER_MIN_CONFIDENCE", "0.58"))
        self.min_probability_margin = float(os.getenv("BTC5M_PAPER_MIN_PROBABILITY_MARGIN", "0.015"))
        self.max_signal_age_ms = max(1, int(os.getenv("BTC5M_PAPER_MAX_SIGNAL_AGE_MS", str(getattr(settings, "max_signal_age_ms", 1200)))))
        self.max_book_age_ms = max(1, int(os.getenv("BTC5M_PAPER_MAX_BOOK_AGE_MS", "2500")))
        self.min_contract_price = float(os.getenv("BTC5M_PAPER_MIN_CONTRACT_PRICE", str(getattr(settings, "min_contract_price", 0.10))))
        self.max_contract_price = float(os.getenv("BTC5M_PAPER_MAX_CONTRACT_PRICE", str(getattr(settings, "max_contract_price", 0.90))))
        self.max_spread = max(0.0, float(os.getenv("BTC5M_PAPER_MAX_SPREAD", str(getattr(settings, "max_spread", 0.06)))))
        self.slippage_buffer = max(0.0, float(os.getenv("BTC5M_PAPER_SLIPPAGE_BUFFER", str(getattr(settings, "slippage_buffer", 0.002)))))
        self.min_depth_multiple = max(1.0, float(os.getenv("BTC5M_PAPER_MIN_DEPTH_MULTIPLE", str(getattr(settings, "min_depth_multiple", 1.25)))))
        self.enforce_depth = self._env_bool("BTC5M_PAPER_REQUIRE_BOOK_DEPTH", True)
        self.depth_levels = max(1, int(getattr(settings, "depth_levels", 5)))
        self.loop_interval_sec = max(0.05, int(os.getenv("BTC5M_PAPER_LOOP_INTERVAL_MS", "200")) / 1000)
        self.scale_weights = self._parse_float_tuple(
            os.getenv("BTC5M_PAPER_SCALE_IN_WEIGHTS", "0.50,0.30,0.20"),
            self.DEFAULT_SCALE_WEIGHTS,
        )
        self.scale_after_sec = self._parse_int_tuple(
            os.getenv("BTC5M_PAPER_SCALE_IN_AFTER_SEC", "0,100,200"),
            self.DEFAULT_SCALE_AFTER_SEC,
        )
        self.stage_min_confidence = self._parse_float_tuple(
            os.getenv("BTC5M_PAPER_SCALE_IN_MIN_CONFIDENCE", "0.58,0.62,0.66"),
            self.DEFAULT_STAGE_MIN_CONFIDENCE,
        )
        self.stage_min_net_edge = self._parse_float_tuple(
            os.getenv("BTC5M_PAPER_SCALE_IN_MIN_NET_EDGE", "0.008,0.012,0.018"),
            self.DEFAULT_STAGE_MIN_NET_EDGE,
        )
        self._validate_scale_config()
        self._init_db()
        self._load_recent()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_float_tuple(raw: str, fallback: tuple[float, ...]) -> tuple[float, ...]:
        try:
            return tuple(float(part.strip()) for part in raw.split(",") if part.strip())
        except ValueError:
            return fallback

    @staticmethod
    def _parse_int_tuple(raw: str, fallback: tuple[int, ...]) -> tuple[int, ...]:
        try:
            return tuple(int(part.strip()) for part in raw.split(",") if part.strip())
        except ValueError:
            return fallback

    def _validate_scale_config(self) -> None:
        if len(self.scale_weights) != 3 or any(weight <= 0 for weight in self.scale_weights):
            self.scale_weights = self.DEFAULT_SCALE_WEIGHTS
        if abs(sum(self.scale_weights) - 1.0) > 1e-9:
            self.scale_weights = self.DEFAULT_SCALE_WEIGHTS
        if len(self.scale_after_sec) != 3 or any(second < 0 for second in self.scale_after_sec):
            self.scale_after_sec = self.DEFAULT_SCALE_AFTER_SEC
        if tuple(sorted(self.scale_after_sec)) != self.scale_after_sec:
            self.scale_after_sec = self.DEFAULT_SCALE_AFTER_SEC
        if len(self.stage_min_confidence) != 3 or any(not 0 <= value <= 1 for value in self.stage_min_confidence):
            self.stage_min_confidence = self.DEFAULT_STAGE_MIN_CONFIDENCE
        if len(self.stage_min_net_edge) != 3 or any(value < 0 for value in self.stage_min_net_edge):
            self.stage_min_net_edge = self.DEFAULT_STAGE_MIN_NET_EDGE
        if not 0 < self.min_contract_price < self.max_contract_price < 1:
            self.min_contract_price, self.max_contract_price = 0.10, 0.90

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
                        scale_stage=1,
                        scale_weight=1.0,
                        outcome=item.outcome,
                        won=item.won,
                        pnl=float(item.pnl),
                    )
                    item.orders = [legacy.to_dict()]
                item.order_count = len(item.orders)
                item.total_notional_usd = round(sum(float(order.get("notional_usd") or 0.0) for order in item.orders), 8)
                item.initial_direction = item.initial_direction or (item.orders[0].get("direction") if item.orders else None)
                item.next_scale_stage = min(4, item.order_count + 1)
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

    @staticmethod
    def _book_depth_usd(book: dict[str, Any]) -> float:
        return round(sum(float(level["price"]) * float(level["size"]) for level in (book.get("ask_levels") or [])), 8)

    @staticmethod
    def _estimate_buy_vwap(book: dict[str, Any], notional_usd: float) -> float | None:
        remaining = float(notional_usd)
        spent = 0.0
        shares = 0.0
        for level in book.get("ask_levels") or []:
            price = float(level["price"])
            size = float(level["size"])
            available_usd = price * size
            take_usd = min(remaining, available_usd)
            if take_usd <= 0 or price <= 0:
                continue
            spent += take_usd
            shares += take_usd / price
            remaining -= take_usd
            if remaining <= 1e-9:
                break
        if remaining > 1e-9 or shares <= 0:
            return None
        return spent / shares

    def _next_stage(self, item: RoundPrediction, elapsed_sec: float) -> tuple[int, float] | None:
        stage = item.order_count + 1
        if stage > len(self.scale_weights):
            return None
        if elapsed_sec < self.scale_after_sec[stage - 1]:
            return None
        return stage, self.scale_weights[stage - 1]

    async def _reject_locked(self, item: RoundPrediction, reason: str, stage: int | None = None) -> None:
        rejection_key = f"{stage or 0}:{reason}"
        if item.last_rejection_key != rejection_key:
            item.rejection_counts[reason] = item.rejection_counts.get(reason, 0) + 1
            item.last_rejection_key = rejection_key
        item.reason = reason
        self.last_reason = reason
        self._save(item)
        await self.publish_state_locked()

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
            changed = False
            if item is None:
                item = RoundPrediction(
                    slug=slug,
                    interval_start_ms=start_ms,
                    interval_end_ms=end_ms,
                    question=str(market.get("question") or ""),
                )
                self.rounds[slug] = item
                changed = True
            if item.btc_open is None:
                item.btc_open = self._first_price_at_or_after(prices, start_ms) or latest[1]
                changed = True
            if changed:
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
                await self._reject_locked(item, "market_open_buffer")
                return
            if timestamp >= end_ms - close_buffer_ms:
                await self._reject_locked(item, "signal_window_closed")
                return

            elapsed_sec = max(0.0, (timestamp - start_ms) / 1000)
            next_stage = self._next_stage(item, elapsed_sec)
            if next_stage is None:
                reason = "scale_in_complete" if item.order_count >= 3 else "waiting_for_next_scale_stage"
                if item.reason != reason:
                    item.reason = reason
                    self.last_reason = reason
                    self._save(item)
                await self.publish_state_locked()
                return
            stage, scale_weight = next_stage
            notional = round(self.max_round_notional_usd * scale_weight, 8)

            selected = self._selected_prediction(snapshot)
            probability_up = float(selected.get("probability_up") or 0.5)
            confidence = float(selected.get("confidence") or 0.0)
            signal_timestamp = int(selected.get("timestamp_ms") or 0)
            signal_age_ms = timestamp - signal_timestamp if signal_timestamp > 0 else self.max_signal_age_ms + 1
            item.probability_up = probability_up
            item.confidence = confidence

            if signal_age_ms < 0 or signal_age_ms > self.max_signal_age_ms:
                await self._reject_locked(item, "signal_stale", stage)
                return
            min_stage_confidence = max(self.min_confidence, self.stage_min_confidence[stage - 1])
            if confidence < min_stage_confidence:
                await self._reject_locked(item, "confidence_too_low", stage)
                return

            if probability_up >= 0.5 + self.min_probability_margin:
                direction = "YES"
            elif probability_up <= 0.5 - self.min_probability_margin:
                direction = "NO"
            else:
                await self._reject_locked(item, "direction_margin_too_low", stage)
                return

            if item.initial_direction is not None and direction != item.initial_direction:
                item.last_direction_change_ms = timestamp
                await self._reject_locked(item, "scale_in_direction_changed", stage)
                return

            books = snapshot.get("books", {}) or {}
            yes_book = books.get(str(market.get("yes_token_id") or ""), {}) or {}
            no_book = books.get(str(market.get("no_token_id") or ""), {}) or {}
            item.yes_ask = yes_book.get("best_ask")
            item.no_ask = no_book.get("best_ask")
            book = yes_book if direction == "YES" else no_book
            entry_price = book.get("best_ask")
            best_bid = book.get("best_bid")
            if entry_price is None:
                await self._reject_locked(item, "contract_price_missing", stage)
                return
            entry_price = float(entry_price)
            if not self.min_contract_price <= entry_price <= self.max_contract_price:
                await self._reject_locked(item, "contract_price_out_of_range", stage)
                return

            book_timestamp = int(book.get("timestamp_ms") or 0)
            book_age_ms = timestamp - book_timestamp if book_timestamp > 0 else self.max_book_age_ms + 1
            if book_age_ms < 0 or book_age_ms > self.max_book_age_ms:
                await self._reject_locked(item, "book_stale", stage)
                return

            spread = max(0.0, entry_price - float(best_bid)) if best_bid is not None else 0.0
            if best_bid is not None and spread > self.max_spread:
                await self._reject_locked(item, "spread_too_wide", stage)
                return

            ask_depth_usd = self._book_depth_usd(book)
            estimated_vwap = self._estimate_buy_vwap(book, notional)
            if self.enforce_depth and (
                ask_depth_usd < notional * self.min_depth_multiple or estimated_vwap is None
            ):
                await self._reject_locked(item, "insufficient_book_depth", stage)
                return

            effective_entry_price = float(estimated_vwap or entry_price)
            expected_probability = probability_up if direction == "YES" else 1 - probability_up
            edge = expected_probability - effective_entry_price
            net_edge = edge - self.slippage_buffer
            min_stage_net_edge = self.stage_min_net_edge[stage - 1]
            quality = {
                "stage": stage,
                "direction": direction,
                "probability_up": round(probability_up, 8),
                "expected_probability": round(expected_probability, 8),
                "confidence": round(confidence, 8),
                "min_confidence": round(min_stage_confidence, 8),
                "entry_price": round(entry_price, 8),
                "estimated_vwap": round(effective_entry_price, 8),
                "edge": round(edge, 8),
                "net_edge": round(net_edge, 8),
                "min_net_edge": round(min_stage_net_edge, 8),
                "spread": round(spread, 8),
                "ask_depth_usd": round(ask_depth_usd, 8),
                "required_depth_usd": round(notional * self.min_depth_multiple, 8),
                "signal_age_ms": signal_age_ms,
                "book_age_ms": book_age_ms,
            }
            item.last_signal_quality = quality
            if net_edge < min_stage_net_edge:
                await self._reject_locked(item, "net_edge_too_low", stage)
                return

            order = PaperMicroOrder(
                order_id=f"{slug}-scale-{stage}-{timestamp}",
                direction=direction,
                entry_price=effective_entry_price,
                notional_usd=notional,
                shares=round(notional / effective_entry_price, 8),
                probability_up=probability_up,
                confidence=confidence,
                created_ms=timestamp,
                scale_stage=stage,
                scale_weight=scale_weight,
                expected_probability=expected_probability,
                edge=edge,
                net_edge=net_edge,
                spread=spread,
                estimated_vwap=estimated_vwap,
                signal_age_ms=signal_age_ms,
                book_age_ms=book_age_ms,
            )
            item.orders.append(order.to_dict())
            item.order_count = len(item.orders)
            item.total_notional_usd = round(sum(float(entry.get("notional_usd") or 0.0) for entry in item.orders), 8)
            item.last_order_ms = timestamp
            item.last_direction = direction
            item.initial_direction = item.initial_direction or direction
            item.next_scale_stage = min(4, item.order_count + 1)
            item.direction = direction
            item.status = "predicted"
            item.reason = f"paper_scale_in_stage_{stage}_placed"
            item.last_rejection_key = None
            item.entry_price = effective_entry_price
            item.notional_usd = item.total_notional_usd
            item.shares = round(sum(float(entry.get("shares") or 0.0) for entry in item.orders), 8)
            item.created_ms = item.created_ms or timestamp
            self.last_reason = item.reason
            self._save(item)

        async with self.state.lock:
            self.state.orders_submitted += 1
            self.state.last_order_result = {
                "mode": "btc_5m_prediction_market_paper_scale_in_guarded",
                "accepted": True,
                "slug": slug,
                "direction": order.direction,
                "entry_price": order.entry_price,
                "notional_usd": order.notional_usd,
                "scale_stage": order.scale_stage,
                "scale_weight": order.scale_weight,
                "edge": order.edge,
                "net_edge": order.net_edge,
                "spread": order.spread,
                "signal_age_ms": order.signal_age_ms,
                "book_age_ms": order.book_age_ms,
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
        rejection_counts: dict[str, int] = {}
        for item in rounds:
            for reason, count in item.rejection_counts.items():
                rejection_counts[reason] = rejection_counts.get(reason, 0) + int(count)
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
            "rejection_counts": dict(sorted(rejection_counts.items())),
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
                "strategy": "BTC_5M_EVENT_SCALE_IN_V2_GUARDED",
                "paper_only": True,
                "scale_in_enabled": True,
                "scale_in_weights": list(self.scale_weights),
                "scale_in_after_sec": list(self.scale_after_sec),
                "stage_min_confidence": list(self.stage_min_confidence),
                "stage_min_net_edge": list(self.stage_min_net_edge),
                "max_entries_per_round": 3,
                "require_same_direction_revalidation": True,
                "require_fresh_signal": True,
                "max_signal_age_ms": self.max_signal_age_ms,
                "require_fresh_book": True,
                "max_book_age_ms": self.max_book_age_ms,
                "require_book_depth": self.enforce_depth,
                "min_depth_multiple": self.min_depth_multiple,
                "max_round_notional_usd": self.max_round_notional_usd,
                "min_contract_price": self.min_contract_price,
                "max_contract_price": self.max_contract_price,
                "max_spread": self.max_spread,
                "slippage_buffer": self.slippage_buffer,
                "close_buffer_sec": self.close_buffer_sec,
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
            await asyncio.sleep(self.loop_interval_sec)
