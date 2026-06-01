from __future__ import annotations

from statistics import pstdev

from .config import Settings
from .models import Direction, TradeIntent, now_ms
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
            return []

        total_weight = sum(max(prediction.confidence, 0.0001) for prediction in fresh)
        fair_up = sum(
            prediction.probability_up * prediction.confidence
            for prediction in fresh
        ) / total_weight
        confidence = min(1.0, sum(prediction.confidence for prediction in fresh) / len(fresh))
        notional = self._notional_for_regime(btc_prices)
        intents: list[TradeIntent] = []

        yes_intent = self._build_intent(
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

        no_intent = self._build_intent(
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

    def _build_intent(
        self,
        *,
        direction: Direction,
        token_id: str,
        fair_probability: float,
        book: object | None,
        confidence: float,
        notional: float,
        now: int,
        source_count: int,
    ) -> TradeIntent | None:
        if book is None:
            return None
        best_ask = getattr(book, "best_ask", None)
        best_bid = getattr(book, "best_bid", None)
        if best_ask is None or best_bid is None:
            return None

        ask = float(best_ask)
        bid = float(best_bid)
        if not self.settings.min_contract_price <= ask <= self.settings.max_contract_price:
            return None

        spread = max(0.0, ask - bid)
        if spread > self.settings.max_spread:
            return None

        raw_edge = fair_probability - ask
        net_edge = raw_edge - spread
        if raw_edge < self.settings.min_edge or net_edge < self.settings.min_net_edge:
            return None

        if not self._cooldown_ok(direction.value, now):
            return None

        return TradeIntent(
            direction=direction,
            token_id=token_id,
            expected_probability=fair_probability,
            market_price=ask,
            edge=net_edge,
            confidence=confidence,
            notional_usd=notional,
            created_ms=now,
            source_count=source_count,
        )

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

        returns = [
            (values[index] / values[index - 1]) - 1
            for index in range(1, len(values))
            if values[index - 1] > 0
        ]
        volatility = pstdev(returns) if len(returns) >= 2 else 0.0
        momentum = abs(values[-1] / values[0] - 1)

        if volatility > 0.0015:
            multiplier = 0.5
        elif momentum > 0.001:
            multiplier = 1.0
        else:
            multiplier = 0.75

        return round(min(base * multiplier, base), 2)
