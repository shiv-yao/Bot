from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from statistics import median
from typing import Any

import websockets

from .logging_utils import log_event
from .models import Prediction, now_ms


class MultiSourceFusion:
    """Fuse BTC momentum while isolating bad data and noisy regimes.

    Fresh prices are compared with the cross-source median. Outliers are removed
    before fusion. A short rolling median-price series then detects choppy,
    abnormally volatile and excessively flat regimes. When quality degrades, a
    neutral prediction is published immediately so an older directional signal
    cannot remain actionable until its normal expiry.
    """

    def __init__(self, settings: Any, state: Any, feeds: Any) -> None:
        self.settings = settings
        self.state = state
        self.feeds = feeds
        self.logger = logging.getLogger("multi_source")
        self._prices: dict[str, deque[tuple[int, float]]] = {
            "chainlink": deque(maxlen=1024),
            "binance": deque(maxlen=1024),
            "coinbase": deque(maxlen=1024),
        }
        self._median_prices: deque[tuple[int, float]] = deque(maxlen=2048)
        self._weights = {
            "chainlink": float(settings.fusion_source_weight_chainlink),
            "binance": float(settings.fusion_source_weight_binance),
            "coinbase": float(settings.fusion_source_weight_coinbase),
        }

    def _setting_float(self, env_name: str, attr_name: str, default: float) -> float:
        return float(os.getenv(env_name, str(getattr(self.settings, attr_name, default))))

    def _setting_int(self, env_name: str, attr_name: str, default: int) -> int:
        return int(os.getenv(env_name, str(getattr(self.settings, attr_name, default))))

    def _setting_bool(self, env_name: str, attr_name: str, default: bool) -> bool:
        raw = os.getenv(env_name)
        if raw is None:
            return bool(getattr(self.settings, attr_name, default))
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @property
    def outlier_max_deviation_bps(self) -> float:
        return max(0.0, self._setting_float("FUSION_OUTLIER_MAX_DEVIATION_BPS", "fusion_outlier_max_deviation_bps", 35.0))

    @property
    def max_dispersion_bps(self) -> float:
        return max(0.0, self._setting_float("FUSION_MAX_DISPERSION_BPS", "fusion_max_dispersion_bps", 20.0))

    @property
    def regime_filter_enabled(self) -> bool:
        return self._setting_bool("FUSION_REGIME_FILTER_ENABLED", "fusion_regime_filter_enabled", True)

    @property
    def regime_window_sec(self) -> int:
        return max(2, self._setting_int("FUSION_REGIME_WINDOW_SEC", "fusion_regime_window_sec", 12))

    @property
    def regime_min_samples(self) -> int:
        return max(3, self._setting_int("FUSION_REGIME_MIN_SAMPLES", "fusion_regime_min_samples", 5))

    @property
    def regime_max_range_bps(self) -> float:
        return max(0.0, self._setting_float("FUSION_REGIME_MAX_RANGE_BPS", "fusion_regime_max_range_bps", 45.0))

    @property
    def regime_min_abs_move_bps(self) -> float:
        return max(0.0, self._setting_float("FUSION_REGIME_MIN_ABS_MOVE_BPS", "fusion_regime_min_abs_move_bps", 1.5))

    @property
    def regime_max_flip_ratio(self) -> float:
        return min(1.0, max(0.0, self._setting_float("FUSION_REGIME_MAX_FLIP_RATIO", "fusion_regime_max_flip_ratio", 0.60)))

    @property
    def regime_min_direction_consistency(self) -> float:
        return min(1.0, max(0.0, self._setting_float("FUSION_REGIME_MIN_DIRECTION_CONSISTENCY", "fusion_regime_min_direction_consistency", 0.60)))

    async def record_price(self, source: str, price: float, timestamp_ms: int | None = None, endpoint: str | None = None) -> None:
        if source not in self._prices or price <= 0:
            return
        timestamp = int(timestamp_ms or now_ms())
        self._prices[source].append((timestamp, float(price)))
        async with self.state.lock:
            current = dict(self.state.source_status.get(source, {}))
            current.update({
                "connected": True,
                "last_price": float(price),
                "last_update_ms": timestamp,
                "age_ms": 0,
                "last_error": None,
            })
            if endpoint:
                current["active_endpoint"] = endpoint
            self.state.external_prices[source] = list(self._prices[source])[-20:]
            self.state.source_status[source] = current
        if self.settings.enable_multi_source_fusion:
            await self._publish_fusion(timestamp)

    async def mark_connected(self, source: str, endpoint: str) -> None:
        async with self.state.lock:
            current = dict(self.state.source_status.get(source, {}))
            current.update({
                "connected": True,
                "active_endpoint": endpoint,
                "last_error": None,
                "reconnect_delay_sec": 0.0,
            })
            self.state.source_status[source] = current

    async def mark_disconnected(self, source: str, error: str, endpoint: str | None = None, reconnect_delay_sec: float | None = None) -> None:
        async with self.state.lock:
            current = dict(self.state.source_status.get(source, {}))
            current["connected"] = False
            current["last_error"] = error
            current["reconnect_count"] = int(current.get("reconnect_count", 0)) + 1
            if endpoint:
                current["active_endpoint"] = endpoint
            if reconnect_delay_sec is not None:
                current["reconnect_delay_sec"] = round(float(reconnect_delay_sec), 3)
            self.state.source_status[source] = current

    async def _update_quality_status(self, samples: list[dict[str, Any]], timestamp: int, median_price: float) -> None:
        async with self.state.lock:
            for sample in samples:
                source = str(sample["source"])
                current = dict(self.state.source_status.get(source, {}))
                current.update({
                    "fusion_median_price": median_price,
                    "fusion_deviation_bps": sample["deviation_bps"],
                    "fusion_outlier": bool(sample["outlier"]),
                    "fusion_quality_update_ms": timestamp,
                })
                self.state.source_status[source] = current

    def _record_median_price(self, timestamp: int, median_price: float) -> None:
        if self._median_prices and self._median_prices[-1][0] == timestamp:
            self._median_prices[-1] = (timestamp, median_price)
        else:
            self._median_prices.append((timestamp, median_price))

    def _regime_snapshot(self, timestamp: int) -> dict[str, Any]:
        cutoff = timestamp - self.regime_window_sec * 1000
        points = [(ts, price) for ts, price in self._median_prices if ts >= cutoff]
        if len(points) < self.regime_min_samples:
            return {
                "status": "warming_up",
                "sample_count": len(points),
                "required_samples": self.regime_min_samples,
                "window_sec": self.regime_window_sec,
                "blocked": False,
            }

        prices = [float(price) for _, price in points]
        moves = [prices[index] / prices[index - 1] - 1 for index in range(1, len(prices)) if prices[index - 1] > 0]
        signs = [1 if move > 0 else -1 if move < 0 else 0 for move in moves]
        non_zero_signs = [sign for sign in signs if sign != 0]
        net_move_bps = (prices[-1] / prices[0] - 1) * 10_000 if prices[0] > 0 else 0.0
        range_bps = (max(prices) / min(prices) - 1) * 10_000 if min(prices) > 0 else 0.0
        net_sign = 1 if net_move_bps > 0 else -1 if net_move_bps < 0 else 0
        direction_consistency = (
            sum(1 for sign in non_zero_signs if sign == net_sign) / len(non_zero_signs)
            if non_zero_signs and net_sign != 0
            else 0.0
        )
        transitions = max(0, len(non_zero_signs) - 1)
        flips = sum(1 for index in range(1, len(non_zero_signs)) if non_zero_signs[index] != non_zero_signs[index - 1])
        flip_ratio = flips / transitions if transitions else 0.0

        if range_bps > self.regime_max_range_bps:
            status = "too_volatile"
        elif abs(net_move_bps) < self.regime_min_abs_move_bps:
            status = "too_flat"
        elif flip_ratio > self.regime_max_flip_ratio or direction_consistency < self.regime_min_direction_consistency:
            status = "choppy"
        else:
            status = "trend_ready"
        return {
            "status": status,
            "sample_count": len(points),
            "required_samples": self.regime_min_samples,
            "window_sec": self.regime_window_sec,
            "net_move_bps": round(net_move_bps, 6),
            "range_bps": round(range_bps, 6),
            "flip_ratio": round(flip_ratio, 6),
            "direction_consistency": round(direction_consistency, 6),
            "blocked": status in {"too_volatile", "too_flat", "choppy"},
        }

    async def _publish_neutral_prediction(self, timestamp: int, reason: str) -> None:
        await self.feeds.upsert_prediction(Prediction(
            source="multi_source_fusion",
            probability_up=0.5,
            confidence=0.0,
            timestamp_ms=timestamp,
        ))
        log_event(self.logger, "fusion_neutralized", reason=reason, timestamp_ms=timestamp)

    async def _publish_fusion(self, timestamp: int) -> None:
        window_start = timestamp - self.settings.external_price_window_sec * 1000
        fresh_cutoff = timestamp - self.settings.external_price_max_age_ms
        raw_samples: list[dict[str, Any]] = []
        for source, prices in self._prices.items():
            if not prices or prices[-1][0] < fresh_cutoff:
                continue
            latest_ts, latest_price = prices[-1]
            reference = next((price for ts, price in prices if ts >= window_start), None)
            if reference is None or reference <= 0:
                continue
            raw_samples.append({
                "source": source,
                "weight": self._weights[source],
                "momentum": latest_price / reference - 1,
                "price": latest_price,
                "timestamp_ms": latest_ts,
            })

        required_sources = int(self.settings.fusion_min_sources)
        if len(raw_samples) < required_sources:
            async with self.state.lock:
                self.state.fusion_snapshot = {
                    "status": "waiting_for_sources",
                    "source_count": len(raw_samples),
                    "clean_source_count": len(raw_samples),
                    "required_sources": required_sources,
                    "samples": raw_samples,
                    "outliers": [],
                    "timestamp_ms": timestamp,
                }
            return

        median_price = float(median(float(sample["price"]) for sample in raw_samples))
        samples: list[dict[str, Any]] = []
        for sample in raw_samples:
            deviation_bps = abs(float(sample["price"]) / median_price - 1) * 10_000 if median_price > 0 else 0.0
            enriched = dict(sample)
            enriched["deviation_bps"] = round(deviation_bps, 6)
            enriched["outlier"] = deviation_bps > self.outlier_max_deviation_bps
            samples.append(enriched)
        await self._update_quality_status(samples, timestamp, median_price)

        clean_samples = [sample for sample in samples if not sample["outlier"]]
        outliers = [sample for sample in samples if sample["outlier"]]
        if len(clean_samples) < required_sources:
            snapshot = {
                "status": "waiting_for_clean_sources",
                "source_count": len(samples),
                "clean_source_count": len(clean_samples),
                "required_sources": required_sources,
                "median_price": median_price,
                "outlier_max_deviation_bps": self.outlier_max_deviation_bps,
                "samples": clean_samples,
                "raw_samples": samples,
                "outliers": outliers,
                "timestamp_ms": timestamp,
            }
            async with self.state.lock:
                self.state.fusion_snapshot = snapshot
            await self._publish_neutral_prediction(timestamp, "waiting_for_clean_sources")
            return

        clean_prices = [float(sample["price"]) for sample in clean_samples]
        dispersion_bps = (max(clean_prices) / min(clean_prices) - 1) * 10_000 if len(clean_prices) > 1 and min(clean_prices) > 0 else 0.0
        total_weight = sum(float(item["weight"]) for item in clean_samples) or 1.0
        fused_momentum = sum(float(item["momentum"]) * float(item["weight"]) for item in clean_samples) / total_weight
        positive = sum(1 for item in clean_samples if float(item["momentum"]) >= 0)
        negative = len(clean_samples) - positive
        agreement = max(positive, negative) / len(clean_samples)
        probability_up = max(0.30, min(0.70, 0.50 + fused_momentum * self.settings.fusion_probability_scale))
        confidence = max(0.0, min(0.90, self.settings.fusion_base_confidence + abs(fused_momentum) * 80 + max(0.0, agreement - 0.5) * 0.30))

        self._record_median_price(timestamp, median_price)
        regime = self._regime_snapshot(timestamp)
        if dispersion_bps > self.max_dispersion_bps:
            status = "price_dispersion_high"
        elif agreement < self.settings.fusion_agreement_threshold:
            status = "low_agreement"
        elif self.regime_filter_enabled and bool(regime.get("blocked")):
            status = f"regime_{regime['status']}"
        else:
            status = "ready"
        snapshot = {
            "status": status,
            "probability_up": probability_up,
            "confidence": confidence,
            "agreement": agreement,
            "fused_momentum": fused_momentum,
            "median_price": median_price,
            "dispersion_bps": round(dispersion_bps, 6),
            "max_dispersion_bps": self.max_dispersion_bps,
            "outlier_max_deviation_bps": self.outlier_max_deviation_bps,
            "source_count": len(samples),
            "clean_source_count": len(clean_samples),
            "outlier_count": len(outliers),
            "required_sources": required_sources,
            "regime": regime,
            "samples": clean_samples,
            "raw_samples": samples,
            "outliers": outliers,
            "timestamp_ms": timestamp,
        }
        async with self.state.lock:
            self.state.fusion_snapshot = snapshot
        if status == "ready":
            await self.feeds.upsert_prediction(Prediction(
                source="multi_source_fusion",
                probability_up=probability_up,
                confidence=confidence,
                timestamp_ms=timestamp,
            ))
        else:
            await self._publish_neutral_prediction(timestamp, status)


def _binance_endpoints(settings: Any) -> list[str]:
    endpoints = [str(settings.binance_ws_url).strip()]
    endpoints.extend(url.strip() for url in str(settings.binance_ws_fallback_urls).split(",") if url.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for endpoint in endpoints:
        if endpoint and endpoint not in seen:
            seen.add(endpoint)
            unique.append(endpoint)
    return unique


def _next_reconnect_delay(settings: Any, current: float) -> float:
    maximum = float(getattr(settings, "source_reconnect_max_delay_sec", 30.0))
    return min(maximum, max(float(settings.source_reconnect_delay_sec), current * 2))


async def binance_ws_loop(settings: Any, state: Any, fusion: MultiSourceFusion) -> None:
    logger = logging.getLogger("binance_ws")
    if not settings.enable_binance_ws:
        return
    endpoints = _binance_endpoints(settings)
    index = 0
    delay = float(settings.source_reconnect_delay_sec)
    while True:
        endpoint = endpoints[index % len(endpoints)]
        try:
            async with websockets.connect(endpoint, ping_interval=20, ping_timeout=10, open_timeout=10) as ws:
                delay = float(settings.source_reconnect_delay_sec)
                await fusion.mark_connected("binance", endpoint)
                log_event(logger, "binance_ws_connected", endpoint=endpoint)
                async for raw in ws:
                    item = json.loads(raw)
                    price = item.get("p")
                    if price is not None:
                        await fusion.record_price("binance", float(price), endpoint=endpoint)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = str(exc)
            await fusion.mark_disconnected("binance", error, endpoint, delay)
            log_event(logger, "binance_ws_error", endpoint=endpoint, error=error, reconnect_delay_sec=delay)
            index = (index + 1) % len(endpoints)
            await asyncio.sleep(delay)
            delay = _next_reconnect_delay(settings, delay)


async def coinbase_ws_loop(settings: Any, state: Any, fusion: MultiSourceFusion) -> None:
    logger = logging.getLogger("coinbase_ws")
    if not settings.enable_coinbase_ws:
        return
    endpoint = settings.coinbase_ws_url
    subscribe = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker"],
    }
    delay = float(settings.source_reconnect_delay_sec)
    while True:
        try:
            async with websockets.connect(endpoint, ping_interval=20, ping_timeout=10, open_timeout=10) as ws:
                await ws.send(json.dumps(subscribe))
                delay = float(settings.source_reconnect_delay_sec)
                await fusion.mark_connected("coinbase", endpoint)
                log_event(logger, "coinbase_ws_connected", endpoint=endpoint)
                async for raw in ws:
                    item = json.loads(raw)
                    if item.get("type") == "ticker" and item.get("product_id") == "BTC-USD" and item.get("price") is not None:
                        await fusion.record_price("coinbase", float(item["price"]), endpoint=endpoint)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = str(exc)
            await fusion.mark_disconnected("coinbase", error, endpoint, delay)
            log_event(logger, "coinbase_ws_error", endpoint=endpoint, error=error, reconnect_delay_sec=delay)
            await asyncio.sleep(delay)
            delay = _next_reconnect_delay(settings, delay)
