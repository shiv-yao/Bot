from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from allocator import allocator
from state import engine

from .alpha_adapters import BaseAlphaAdapter, FusionAlphaAdapter, RankedCandidatesAdapter, StaticSignalMerger
from .models import OrderIntent, SignalEvent
from .portfolio import PortfolioBook
from .signal_bus import SignalBus


class TradingOrchestrator:
    """Thin orchestration layer that keeps the old modules but standardizes flow.

    Flow:
        candidates -> alpha adapters -> signal bus -> merged signals
        -> allocator -> order intents -> execution / portfolio / analytics
    """

    def __init__(self, alpha_adapters: Optional[Sequence[BaseAlphaAdapter]] = None):
        self.engine = engine
        self.bus = SignalBus()
        self.alpha_adapters = list(alpha_adapters or [FusionAlphaAdapter(), RankedCandidatesAdapter()])
        self.merger = StaticSignalMerger()
        self.portfolio = PortfolioBook(self.engine)

    async def collect_signals(self, candidates: set, smart_wallets: Optional[Sequence[str]] = None) -> List[SignalEvent]:
        produced = []
        for adapter in self.alpha_adapters:
            try:
                sig = await adapter.generate(candidates, smart_wallets)
                if sig:
                    await self.bus.publish(sig)
                    produced.append(sig)
            except Exception as exc:
                self.engine.stats["errors"] += 1
                self.engine.log(f"ALPHA_ERR {adapter.name}: {exc}")

        drained = await self.bus.drain()
        merged = self.merger.merge(drained)
        self.engine.candidate_count = len(candidates)
        return merged

    def build_orders(self, signals: Iterable[SignalEvent]) -> List[OrderIntent]:
        orders: List[OrderIntent] = []
        capital = max(float(getattr(self.engine, "capital", 0.0) or 0.0), 0.0)
        for sig in signals:
            strategy_name = sig.strategy or "fusion"
            weight = float(allocator.weight(strategy_name if strategy_name in allocator.performance else "stable"))
            size_sol = max(0.0, capital * 0.02 * max(weight, 0.05))
            orders.append(OrderIntent(
                mint=sig.mint,
                side=sig.side,
                size_sol=size_sol,
                strategy=strategy_name,
                signal_score=sig.score,
                source=sig.source,
                metadata={
                    "confidence": sig.confidence,
                    "allocator_weight": weight,
                },
            ))
        return orders


def build_default_orchestrator() -> TradingOrchestrator:
    return TradingOrchestrator()
