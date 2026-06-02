from __future__ import annotations

import json
from typing import Any

import aiohttp


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return decoded
    return []


def _first_market(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list) and payload:
        return payload[0] if isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        return payload
    return None


class EventSettlementResolver:
    def __init__(self, gamma_api_url: str, timeout_sec: float = 3.0) -> None:
        self.gamma_api_url = gamma_api_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self._cache: dict[str, dict[str, float]] = {}

    async def settlement_prices(self, market_slug: str) -> dict[str, float] | None:
        if not market_slug:
            return None
        cached = self._cache.get(market_slug)
        if cached is not None:
            return cached
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.gamma_api_url}/markets", params={"slug": market_slug}) as response:
                response.raise_for_status()
                market = _first_market(await response.json())
        if not market:
            return None
        prices = self._extract_settlement_prices(market)
        if prices is not None:
            self._cache[market_slug] = prices
        return prices

    def _extract_settlement_prices(self, market: dict[str, Any]) -> dict[str, float] | None:
        token_ids = [str(item) for item in _decode_list(market.get("clobTokenIds") or market.get("clob_token_ids"))]
        outcomes = [str(item).lower() for item in _decode_list(market.get("outcomes", ["Up", "Down"]))]
        if len(token_ids) < 2:
            return None

        raw_prices = _decode_list(market.get("outcomePrices") or market.get("outcome_prices"))
        if len(raw_prices) >= 2:
            try:
                values = [float(raw_prices[0]), float(raw_prices[1])]
            except (TypeError, ValueError):
                values = []
            if len(values) >= 2 and (max(values) >= 0.98 or min(values) <= 0.02):
                return {token_ids[0]: round(values[0], 8), token_ids[1]: round(values[1], 8)}

        winner = str(
            market.get("winningOutcome")
            or market.get("winning_outcome")
            or market.get("winner")
            or market.get("resolvedOutcome")
            or ""
        ).lower()
        if winner and outcomes:
            for index, outcome in enumerate(outcomes[:2]):
                if winner == outcome or winner in outcome or outcome in winner:
                    return {token_ids[0]: 1.0 if index == 0 else 0.0, token_ids[1]: 1.0 if index == 1 else 0.0}

        return None
