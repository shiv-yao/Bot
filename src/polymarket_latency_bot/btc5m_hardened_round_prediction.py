from __future__ import annotations

import os
from typing import Any

from .btc5m_round_prediction import BTC5mRoundPredictionEngine, PaperMicroOrder, RoundPrediction
from .models import now_ms


class BTC5mHardenedRoundPredictionEngine(BTC5mRoundPredictionEngine):
    """Paper-only BTC 5m engine with stricter scale-in and data-quality guards.

    This layer preserves the original database and public payload shape while
    adding fail-closed book checks, BTC open/close quality checks, persistent
    confirmation for later stages, anti-chasing limits, source requirements,
    order-book imbalance filters and observational Shadow A/B profiles.
    """

    SHADOW_PROFILE_EDGE_BONUS = {
        "baseline": (0.0, 0.0, 0.0),
        "conservative": (0.0, 0.003, 0.004),
        "strict": (0.0, 0.006, 0.010),
    }

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        self.signal_samples: dict[str, list[dict[str, Any]]] = {}
        self.shadow_decisions: dict[tuple[str, int, str], dict[str, Any]] = {}
        super().__init__(settings, state, db_path=db_path)
        self.open_price_max_delay_ms = max(0, int(os.getenv("BTC5M_PAPER_OPEN_PRICE_MAX_DELAY_MS", "2000")))
        self.settlement_max_delay_ms = max(0, int(os.getenv("BTC5M_PAPER_SETTLEMENT_MAX_DELAY_MS", "2000")))
        self.stage_confirm_samples = self._validated_int_tuple("BTC5M_PAPER_STAGE_CONFIRM_SAMPLES", "1,3,5", (1, 3, 5), minimum=1)
        self.stage_confirm_window_sec = self._validated_int_tuple("BTC5M_PAPER_STAGE_CONFIRM_WINDOW_SEC", "0,8,15", (0, 8, 15), minimum=0)
        self.stage_max_direction_flips = self._validated_int_tuple("BTC5M_PAPER_STAGE_MAX_DIRECTION_FLIPS", "0,0,0", (0, 0, 0), minimum=0)
        self.stage_max_price_worsening = self._validated_float_tuple("BTC5M_PAPER_SCALE_IN_MAX_PRICE_WORSENING", "0,0.025,0.015", (0.0, 0.025, 0.015), minimum=0.0)
        self.stage_max_edge_decay = self._validated_float_tuple("BTC5M_PAPER_SCALE_IN_MAX_EDGE_DECAY", "0,0.004,0.002", (0.0, 0.004, 0.002), minimum=0.0)
        self.stage_min_clean_sources = self._validated_int_tuple("BTC5M_PAPER_STAGE_MIN_CLEAN_SOURCES", "1,2,3", (1, 2, 3), minimum=1)
        self.stage_require_fusion = self._validated_bool_tuple("BTC5M_PAPER_STAGE_REQUIRE_FUSION", "false,true,true", (False, True, True))
        self.stage_min_book_imbalance = self._validated_float_tuple("BTC5M_PAPER_STAGE_MIN_BOOK_IMBALANCE", "0.20,0.30,0.35", (0.20, 0.30, 0.35), minimum=0.0, maximum=1.0)
        self.shadow_enabled = self._env_bool("BTC5M_PAPER_SHADOW_AB_ENABLED", True)

    def _validated_int_tuple(self, name: str, raw_default: str, fallback: tuple[int, int, int], *, minimum: int) -> tuple[int, int, int]:
        values = self._parse_int_tuple(os.getenv(name, raw_default), fallback)
        if len(values) != 3 or any(value < minimum for value in values):
            return fallback
        return tuple(int(value) for value in values)  # type: ignore[return-value]

    def _validated_float_tuple(
        self,
        name: str,
        raw_default: str,
        fallback: tuple[float, float, float],
        *,
        minimum: float,
        maximum: float | None = None,
    ) -> tuple[float, float, float]:
        values = self._parse_float_tuple(os.getenv(name, raw_default), fallback)
        if len(values) != 3 or any(value < minimum or (maximum is not None and value > maximum) for value in values):
            return fallback
        return tuple(float(value) for value in values)  # type: ignore[return-value]

    @staticmethod
    def _validated_bool_tuple(name: str, raw_default: str, fallback: tuple[bool, bool, bool]) -> tuple[bool, bool, bool]:
        raw = os.getenv(name, raw_default)
        parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
        if len(parts) != 3 or any(part not in {"1", "0", "true", "false", "yes", "no", "on", "off"} for part in parts):
            return fallback
        return tuple(part in {"1", "true", "yes", "on"} for part in parts)  # type: ignore[return-value]

    @staticmethod
    def _first_sample_at_or_after(prices: list[tuple[int, float]], target_ms: int) -> tuple[int, float] | None:
        return next(((int(timestamp), float(price)) for timestamp, price in prices if timestamp >= target_ms), None)

    @staticmethod
    def _side_depth_usd(levels: list[dict[str, Any]] | None, limit: int | None = None) -> float:
        selected = list(levels or [])[:limit]
        return round(sum(float(level["price"]) * float(level["size"]) for level in selected), 8)

    @classmethod
    def _book_metrics(cls, book: dict[str, Any]) -> dict[str, float]:
        bid_levels = list(book.get("bid_levels") or [])
        ask_levels = list(book.get("ask_levels") or [])
        bid_depth = cls._side_depth_usd(bid_levels)
        ask_depth = cls._side_depth_usd(ask_levels)
        bid_depth_3 = cls._side_depth_usd(bid_levels, 3)
        ask_depth_3 = cls._side_depth_usd(ask_levels, 3)
        total = bid_depth + ask_depth
        total_3 = bid_depth_3 + ask_depth_3
        imbalance = bid_depth / total if total > 0 else 0.0
        imbalance_3 = bid_depth_3 / total_3 if total_3 > 0 else 0.0
        concentration = (bid_depth_3 + ask_depth_3) / total if total > 0 else 0.0
        return {
            "bid_depth_usd": round(bid_depth, 8),
            "ask_depth_usd": round(ask_depth, 8),
            "top3_bid_depth_usd": round(bid_depth_3, 8),
            "top3_ask_depth_usd": round(ask_depth_3, 8),
            "book_imbalance": round(imbalance, 8),
            "top3_book_imbalance": round(imbalance_3, 8),
            "liquidity_concentration": round(concentration, 8),
        }

    def _record_signal_sample(self, slug: str, *, timestamp: int, direction: str, net_edge: float) -> None:
        samples = self.signal_samples.setdefault(slug, [])
        if samples and timestamp - int(samples[-1]["timestamp_ms"]) < 1000:
            samples[-1] = {"timestamp_ms": timestamp, "direction": direction, "net_edge": float(net_edge)}
        else:
            samples.append({"timestamp_ms": timestamp, "direction": direction, "net_edge": float(net_edge)})
        cutoff = timestamp - 60_000
        self.signal_samples[slug] = [sample for sample in samples if int(sample["timestamp_ms"]) >= cutoff]

    def _stage_confirmation(self, slug: str, *, timestamp: int, stage: int, direction: str, min_net_edge: float) -> dict[str, Any]:
        required_samples = self.stage_confirm_samples[stage - 1]
        window_ms = self.stage_confirm_window_sec[stage - 1] * 1000
        cutoff = timestamp - window_ms if window_ms > 0 else timestamp
        samples = [sample for sample in self.signal_samples.get(slug, []) if int(sample["timestamp_ms"]) >= cutoff]
        same_direction = [sample for sample in samples if sample.get("direction") == direction]
        flips = sum(1 for left, right in zip(samples, samples[1:]) if left.get("direction") != right.get("direction"))
        average_net_edge = sum(float(sample.get("net_edge") or 0.0) for sample in same_direction) / max(1, len(same_direction))
        confirmed = (
            len(same_direction) >= required_samples
            and flips <= self.stage_max_direction_flips[stage - 1]
            and average_net_edge >= min_net_edge
        )
        return {
            "confirmed": confirmed,
            "samples": len(same_direction),
            "required_samples": required_samples,
            "window_sec": self.stage_confirm_window_sec[stage - 1],
            "direction_flips": flips,
            "max_direction_flips": self.stage_max_direction_flips[stage - 1],
            "average_net_edge": round(average_net_edge, 8),
        }

    def _record_shadow_decisions(self, item: RoundPrediction, quality: dict[str, Any], notional: float) -> None:
        if not self.shadow_enabled:
            return
        stage = int(quality["stage"])
        for profile, bonuses in self.SHADOW_PROFILE_EDGE_BONUS.items():
            threshold = float(self.stage_min_net_edge[stage - 1]) + float(bonuses[stage - 1])
            if float(quality["net_edge"]) < threshold:
                continue
            key = (item.slug, stage, profile)
            if key in self.shadow_decisions:
                continue
            self.shadow_decisions[key] = {
                "slug": item.slug,
                "profile": profile,
                "stage": stage,
                "direction": quality["direction"],
                "entry_price": quality["estimated_vwap"],
                "notional_usd": notional,
                "net_edge": quality["net_edge"],
                "threshold": round(threshold, 8),
                "created_ms": now_ms(),
            }

    def _shadow_summary_locked(self) -> dict[str, Any]:
        outcomes = {item.slug: item.outcome for item in self.rounds.values() if item.status == "settled" and item.outcome in {"YES", "NO"}}
        profiles: dict[str, dict[str, Any]] = {}
        for profile in self.SHADOW_PROFILE_EDGE_BONUS:
            rows = [row for row in self.shadow_decisions.values() if row["profile"] == profile and row["slug"] in outcomes]
            wins = 0
            losses = 0
            pnl = 0.0
            notional = 0.0
            for row in rows:
                entry = float(row["entry_price"])
                stake = float(row["notional_usd"])
                shares = stake / entry if entry > 0 else 0.0
                won = row["direction"] == outcomes[row["slug"]]
                wins += 1 if won else 0
                losses += 0 if won else 1
                pnl += shares * (1.0 - entry) if won else -stake
                notional += stake
            profiles[profile] = {
                "settled_orders": len(rows),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / max(1, wins + losses), 6),
                "realized_pnl": round(pnl, 8),
                "total_notional_usd": round(notional, 8),
                "realized_ev": round(pnl / max(1e-9, notional), 8) if notional else 0.0,
            }
        return {"enabled": self.shadow_enabled, "profiles": profiles, "note": "Observational only. Shadow profiles never create Paper positions."}

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
                item = RoundPrediction(slug=slug, interval_start_ms=start_ms, interval_end_ms=end_ms, question=str(market.get("question") or ""))
                self.rounds[slug] = item
                changed = True
            if item.btc_open is None:
                open_sample = self._first_sample_at_or_after(prices, start_ms)
                if open_sample is None:
                    if latest[0] <= start_ms + self.open_price_max_delay_ms:
                        self.last_reason = "waiting_for_btc_open"
                        await self.publish_state_locked()
                        return
                    item.status = "skipped"
                    item.reason = "invalid_btc_open_missing"
                    item.last_signal_quality = {"settlement_quality": "invalid_data", "settlement_reason": item.reason}
                    self._save(item)
                    await self.publish_state_locked()
                    return
                open_delay_ms = int(open_sample[0] - start_ms)
                if open_delay_ms > self.open_price_max_delay_ms:
                    item.status = "skipped"
                    item.reason = "invalid_btc_open_delayed"
                    item.last_signal_quality = {"settlement_quality": "invalid_data", "btc_open_age_ms": open_delay_ms, "settlement_reason": item.reason}
                    self._save(item)
                    await self.publish_state_locked()
                    return
                item.btc_open = float(open_sample[1])
                item.last_signal_quality = {"settlement_quality": "open_valid", "btc_open_age_ms": open_delay_ms, "settlement_source": "btc_price_stream"}
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
            probability_up = float(selected.get("probability_up") if selected.get("probability_up") is not None else 0.5)
            confidence = float(selected.get("confidence") if selected.get("confidence") is not None else 0.0)
            signal_timestamp = int(selected.get("timestamp_ms") or 0)
            signal_source = str(selected.get("source") or "unknown")
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

            fusion = snapshot.get("fusion_snapshot", {}) or {}
            clean_sources = int(fusion.get("clean_source_count", fusion.get("source_count", 0)) or 0)
            fusion_ready = str(fusion.get("status") or "") == "ready"
            if clean_sources < self.stage_min_clean_sources[stage - 1]:
                await self._reject_locked(item, "insufficient_clean_sources", stage)
                return
            if self.stage_require_fusion[stage - 1] and (not fusion_ready or signal_source not in {"multi_source_fusion", "fusion_snapshot"}):
                await self._reject_locked(item, "fusion_required_for_scale_in", stage)
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
            if best_bid is None:
                await self._reject_locked(item, "best_bid_missing", stage)
                return
            entry_price = float(entry_price)
            best_bid = float(best_bid)
            if best_bid <= 0 or best_bid >= entry_price:
                await self._reject_locked(item, "invalid_best_bid", stage)
                return
            if not self.min_contract_price <= entry_price <= self.max_contract_price:
                await self._reject_locked(item, "contract_price_out_of_range", stage)
                return
            book_timestamp = int(book.get("timestamp_ms") or 0)
            book_age_ms = timestamp - book_timestamp if book_timestamp > 0 else self.max_book_age_ms + 1
            if book_age_ms < 0 or book_age_ms > self.max_book_age_ms:
                await self._reject_locked(item, "book_stale", stage)
                return
            spread = max(0.0, entry_price - best_bid)
            if spread > self.max_spread:
                await self._reject_locked(item, "spread_too_wide", stage)
                return

            book_metrics = self._book_metrics(book)
            ask_depth_usd = float(book_metrics["ask_depth_usd"])
            estimated_vwap = self._estimate_buy_vwap(book, notional)
            if self.enforce_depth and (ask_depth_usd < notional * self.min_depth_multiple or estimated_vwap is None):
                await self._reject_locked(item, "insufficient_book_depth", stage)
                return
            if float(book_metrics["book_imbalance"]) < self.stage_min_book_imbalance[stage - 1]:
                await self._reject_locked(item, "book_imbalance_too_weak", stage)
                return

            effective_entry_price = float(estimated_vwap or entry_price)
            expected_probability = probability_up if direction == "YES" else 1 - probability_up
            edge = expected_probability - effective_entry_price
            net_edge = edge - self.slippage_buffer
            min_stage_net_edge = self.stage_min_net_edge[stage - 1]
            self._record_signal_sample(slug, timestamp=timestamp, direction=direction, net_edge=net_edge)
            confirmation = self._stage_confirmation(slug, timestamp=timestamp, stage=stage, direction=direction, min_net_edge=min_stage_net_edge)
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
                "required_depth_usd": round(notional * self.min_depth_multiple, 8),
                "signal_age_ms": signal_age_ms,
                "book_age_ms": book_age_ms,
                "signal_source": signal_source,
                "clean_sources": clean_sources,
                "fusion_ready": fusion_ready,
                "confirmation": confirmation,
                **book_metrics,
            }
            item.last_signal_quality = quality
            self._record_shadow_decisions(item, quality, notional)
            if net_edge < min_stage_net_edge:
                await self._reject_locked(item, "net_edge_too_low", stage)
                return
            if not confirmation["confirmed"]:
                await self._reject_locked(item, "stage_confirmation_pending", stage)
                return
            if item.orders:
                previous = item.orders[-1]
                price_worsening = effective_entry_price - float(previous.get("entry_price") or 0.0)
                edge_decay = float(previous.get("net_edge") or 0.0) - net_edge
                quality["price_worsening"] = round(price_worsening, 8)
                quality["max_price_worsening"] = round(self.stage_max_price_worsening[stage - 1], 8)
                quality["edge_decay"] = round(edge_decay, 8)
                quality["max_edge_decay"] = round(self.stage_max_edge_decay[stage - 1], 8)
                if price_worsening > self.stage_max_price_worsening[stage - 1]:
                    await self._reject_locked(item, "scale_in_price_worsened", stage)
                    return
                if edge_decay > self.stage_max_edge_decay[stage - 1]:
                    await self._reject_locked(item, "scale_in_edge_decayed", stage)
                    return

            order = PaperMicroOrder(
                order_id=f"{slug}-scale-{stage}-{timestamp}", direction=direction, entry_price=effective_entry_price,
                notional_usd=notional, shares=round(notional / effective_entry_price, 8), probability_up=probability_up,
                confidence=confidence, created_ms=timestamp, scale_stage=stage, scale_weight=scale_weight,
                expected_probability=expected_probability, edge=edge, net_edge=net_edge, spread=spread,
                estimated_vwap=estimated_vwap, signal_age_ms=signal_age_ms, book_age_ms=book_age_ms, signal_source=signal_source,
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
            self.state.last_order_result = {"mode": "btc_5m_prediction_market_paper_scale_in_hardened", "accepted": True, "slug": slug, "direction": order.direction, "entry_price": order.entry_price, "notional_usd": order.notional_usd, "scale_stage": order.scale_stage, "scale_weight": order.scale_weight, "edge": order.edge, "net_edge": order.net_edge, "spread": order.spread, "signal_age_ms": order.signal_age_ms, "book_age_ms": order.book_age_ms, "round_order_count": item.order_count, "round_total_notional_usd": item.total_notional_usd}
        await self.publish_state()

    async def settle_due_rounds(self, prices: list[tuple[int, float]] | None = None) -> None:
        prices = prices if prices is not None else await self._btc_prices()
        latest = self._latest_price(prices)
        if latest is None:
            return
        timestamp, _ = latest
        changed = False
        async with self.lock:
            for item in self.rounds.values():
                if item.status not in {"predicted", "collecting"} or timestamp < item.interval_end_ms:
                    continue
                close_sample = self._first_sample_at_or_after(prices, item.interval_end_ms)
                if close_sample is None:
                    if timestamp <= item.interval_end_ms + self.settlement_max_delay_ms:
                        continue
                    item.status = "skipped"
                    item.reason = "invalid_btc_close_missing"
                    item.pnl = 0.0
                    item.last_signal_quality = {**dict(item.last_signal_quality or {}), "settlement_quality": "invalid_data", "settlement_reason": item.reason}
                    self._save(item)
                    changed = True
                    continue
                close_delay_ms = int(close_sample[0] - item.interval_end_ms)
                if close_delay_ms > self.settlement_max_delay_ms:
                    item.status = "skipped"
                    item.reason = "invalid_btc_close_delayed"
                    item.pnl = 0.0
                    item.last_signal_quality = {**dict(item.last_signal_quality or {}), "settlement_quality": "invalid_data", "btc_close_age_ms": close_delay_ms, "settlement_reason": item.reason}
                    self._save(item)
                    changed = True
                    continue
                item.btc_close = float(close_sample[1])
                if item.btc_open is None:
                    item.status = "skipped"
                    item.reason = "invalid_btc_open_missing"
                    item.pnl = 0.0
                    self._save(item)
                    changed = True
                    continue
                item.outcome = "YES" if item.btc_close > item.btc_open else "NO" if item.btc_close < item.btc_open else "FLAT"
                item.settled_ms = timestamp
                item.last_signal_quality = {**dict(item.last_signal_quality or {}), "settlement_quality": "valid", "settlement_source": "btc_price_stream", "btc_close_age_ms": close_delay_ms}
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
                                order.pnl = round(order.shares * (1.0 - order.entry_price), 8)
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

    async def publish_state_locked(self) -> None:
        await super().publish_state_locked()
        shadow = self._shadow_summary_locked()
        async with self.state.lock:
            paper = dict(self.state.paper_portfolio or {})
            rules = dict(paper.get("rules") or {})
            rules.update({
                "strategy": "BTC_5M_EVENT_SCALE_IN_V4_HARDENED",
                "open_price_max_delay_ms": self.open_price_max_delay_ms,
                "settlement_max_delay_ms": self.settlement_max_delay_ms,
                "stage_confirm_samples": list(self.stage_confirm_samples),
                "stage_confirm_window_sec": list(self.stage_confirm_window_sec),
                "stage_max_direction_flips": list(self.stage_max_direction_flips),
                "stage_max_price_worsening": list(self.stage_max_price_worsening),
                "stage_max_edge_decay": list(self.stage_max_edge_decay),
                "stage_min_clean_sources": list(self.stage_min_clean_sources),
                "stage_require_fusion": list(self.stage_require_fusion),
                "stage_min_book_imbalance": list(self.stage_min_book_imbalance),
                "shadow_ab_enabled": self.shadow_enabled,
            })
            paper["rules"] = rules
            paper["shadow_ab"] = shadow
            self.state.paper_portfolio = paper
