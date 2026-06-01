from __future__ import annotations

from statistics import pstdev
from typing import Any

from .config import Settings
from .models import BookTop, Direction, TradeIntent, now_ms
from .state import BotState


class LatencyStrategy:
    def __init__(self, settings: Settings, state: BotState) -> None:
        self.settings = settings
        self.state = state
        self._last_signal_ms: dict[str, int] = {}

    async def build_intents(self) -> list[TradeIntent]:
        async with self.state.lock:
            now = now_ms()
            fresh = [
                prediction
                for prediction in self.state.predictions.values()
                if now - prediction.timestamp_ms <= self.settings.max_signal_age_ms
                and prediction.confidence >= self.settings.min_confidence
            ]
            yes_book = self.state.books.get(self.settings.yes_token_id)
            no_book = self.state.books.get(self.settings.no_token_id)
            btc_prices = list(self.state.btc_prices)

        if not fresh:
            await self._reject("no_fresh_prediction", {"now_ms": now})
            return []

        total_weight = sum(max(prediction.confidence, 0.0001) for prediction in fresh)
        fair_up = sum(prediction.probability_up * prediction.confidence for prediction in fresh) / total_weight
        confidence = min(1.0, sum(prediction.confidence for prediction in fresh) / len(fresh))
        notional = self._notional_for_regime(btc_prices)
        intents: list[TradeIntent] = []

        yes_intent = await self._build_intent(
            direction=Direction.BUY_YES,
            token_id=self.settings.yes_token_id,
            fair_probability=fair_up,
            book=yes_book,
            confidence=confidence,
            notional=notional,
            now=now,
            source_count=len(fresh),
        )
        if yes_intent is not None:
            intents.append(yes_intent)

        no_intent = await self._build_intent(
            direction=Direction.BUY_NO,
            token_id=self.settings.no_token_id,
            fair_probability=1 - fair_up,
            book=no_book,
            confidence=confidence,
            notional=notional,
            now=now,
            source_count=len(fresh),
        )
        if no_intent is not None:
            intents.append(no_intent)

        return intents

    async def _build_intent(
        self,
        *,
        direction: Direction,
        token_id: str,
        fair_probability: float,
        book: BookTop | None,
        confidence: float,
        notional: float,
        now: int,
        source_count: int,
    ) -> TradeIntent | None:
        snapshot: dict[str, Any] = {
            "direction": direction.value,
            "token_id": token_id,
            "fair_probability": fair_probability,
            "confidence": confidence,
            "notional_usd": notional,
            "timestamp_ms": now,
        }
        if book is None:
            return await self._reject("book_missing", snapshot)
        if book.best_ask is None or book.best_bid is None:
            return await self._reject("best_price_missing", snapshot)

        ask = float(book.best_ask)
        bid = float(book.best_bid)
        spread = max(0.0, ask - bid)
        ask_depth = book.ask_depth_usd(self.settings.depth_levels)
        estimated_vwap = book.estimate_buy_vwap(notional, self.settings.depth_levels)
        snapshot.update({
            "best_ask": ask,
            "best_bid": bid,
            "spread": spread,
            "ask_depth_usd": ask_depth,
            "depth_levels": self.settings.depth_levels,
            "estimated_vwap": estimated_vwap,
        })

        if not self.settings.min_contract_price <= ask <= self.settings.max_contract_price:
            return await self._reject("contract_price_out_of_range", snapshot)
        if spread > self.settings.max_spread:
            return await self._reject("spread_too_wide", snapshot)
        if not book.ask_levels:
            return await self._reject("depth_snapshot_missing", snapshot)
        if ask_depth < notional * self.settings.min_depth_multiple:
            return await self._reject("insufficient_depth", snapshot)
        if estimated_vwap is None:
            return await self._reject("vwap_unavailable", snapshot)

        slippage = max(0.0, estimated_vwap - ask)
        raw_edge = fair_probability - estimated_vwap
        net_edge = raw_edge - spread - self.settings.slippage_buffer
        snapshot.update({
            "slippage": slippage,
            "raw_edge_after_vwap": raw_edge,
            "net_edge_after_costs": net_edge,
            "slippage_buffer": self.settings.slippage_buffer,
        })

        if slippage > self.settings.max_slippage:
            return await self._reject("slippage_too_high", snapshot)
        if raw_edge < self.settings.min_edge:
            return await self._reject("raw_edge_too_low", snapshot)
        if net_edge < self.settings.min_net_edge:
            return await self._reject("net_edge_too_low", snapshot)
        if not self._cooldown_ok(direction.value, now):
            return await self._reject("signal_cooldown", snapshot)

        intent = TradeIntent(
            direction=direction,
            token_id=token_id,
            expected_probability=fair_probability,
            market_price=float(estimated_vwap),
            edge=net_edge,
            confidence=confidence,
            notional_usd=notional,
            created_ms=now,
            source_count=source_count,
            spread=spread,
            estimated_vwap=float(estimated_vwap),
            slippage=slippage,
            ask_depth_usd=ask_depth,
        )
        async with self.state.lock:
            self.state.last_strategy_snapshot = {**snapshot, "decision": "accepted"}
        return intent

    async def _reject(self, reason: str, snapshot: dict[str, Any]) -> None:
        async with self.state.lock:
            self.state.strategy_rejections[reason] = self.state.strategy_rejections.get(reason, 0) + 1
            self.state.last_strategy_snapshot = {**snapshot, "decision": "rejected", "reason": reason}
        return None

    def _cooldown_ok(self, key: str, now: int) -> bool:
        previous = self._last_signal_ms.get(key, 0)
        if now - previous < self.settings.signal_cooldown_ms:
            return False
        self._last_signal_ms[key] = now
        return True

    def _notional_for_regime(self, prices: list[tuple[int, float]]) -> float:
        base = self.settings.account_equity_usd * self.settings.max_order_equity_fraction
        values = [price for _, price in prices[-30:]]
        if len(values) < 5 or values[0] <= 0:
            return round(base, 2)
        returns = [(values[i] / values[i - 1]) - 1 for i in range(1, len(values)) if values[i - 1] > 0]
        volatility = pstdev(returns) if len(returns) >= 2 else 0.0
        momentum = abs(values[-1] / values[0] - 1)
        multiplier = 0.5 if volatility > 0.0015 else 1.0 if momentum > 0.001 else 0.75
        return round(min(base * multiplier, base), 2)
