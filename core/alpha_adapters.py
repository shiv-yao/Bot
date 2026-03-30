from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from .models import SignalEvent


class BaseAlphaAdapter:
    name = "base_alpha"

    async def generate(self, candidates: set, smart_wallets: Optional[Sequence[str]] = None) -> Optional[SignalEvent]:
        raise NotImplementedError


class FusionAlphaAdapter(BaseAlphaAdapter):
    name = "alpha_boost_v3"

    async def generate(self, candidates: set, smart_wallets: Optional[Sequence[str]] = None) -> Optional[SignalEvent]:
        from alpha_boost_v3 import alpha_fusion

        mint, score, source = await alpha_fusion(candidates)
        if not mint:
            return None

        return SignalEvent(
            mint=mint,
            side="BUY",
            score=float(score or 0.0),
            source=source or self.name,
            confidence=min(max(float(score or 0.0) / 1500.0, 0.0), 1.0),
            strategy="fusion",
            metadata={"candidate_count": len(candidates)},
        )


class RankedCandidatesAdapter(BaseAlphaAdapter):
    name = "alpha_engine"

    async def generate(self, candidates: set, smart_wallets: Optional[Sequence[str]] = None) -> Optional[SignalEvent]:
        from alpha_engine import rank_candidates

        ranked = await rank_candidates(candidates)
        if not ranked:
            return None

        top = ranked[0]
        score = float(top.get("score", 0.0) or 0.0)
        mint = top.get("mint")
        if not mint:
            return None

        return SignalEvent(
            mint=mint,
            side="BUY",
            score=score,
            source=self.name,
            confidence=min(max(score / 100.0, 0.0), 1.0),
            strategy="breakout",
            metadata={"top_n": ranked[:3]},
        )


class StaticSignalMerger:
    """Combine multiple alpha outputs without deleting any source alpha."""

    def merge(self, signals: Iterable[SignalEvent]) -> List[SignalEvent]:
        by_mint = {}
        for sig in signals:
            if sig is None:
                continue

            existing = by_mint.get(sig.mint)
            if existing is None:
                by_mint[sig.mint] = sig
                continue

            existing.score += sig.score
            existing.confidence = min(1.0, (existing.confidence + sig.confidence) / 2.0)
            existing.metadata.setdefault("merged_sources", [existing.source])
            existing.metadata["merged_sources"].append(sig.source)
            existing.source = "+".join(sorted(set(existing.metadata["merged_sources"])))

        return sorted(by_mint.values(), key=lambda x: x.score, reverse=True)
