from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import FillEvent


@dataclass
class PositionRecord:
    mint: str
    amount: float = 0.0
    entry_price: float = 0.0
    last_price: float = 0.0
    peak_price: float = 0.0
    strategy: str = "unassigned"
    source: str = "unknown"
    tx_sigs: List[str] = field(default_factory=list)

    @property
    def market_value(self) -> float:
        return self.amount * self.last_price

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.last_price - self.entry_price) / self.entry_price


class PortfolioBook:
    def __init__(self, engine):
        self.engine = engine
        self.positions: Dict[str, PositionRecord] = {}
        self._rehydrate_from_engine()

    def _rehydrate_from_engine(self) -> None:
        for p in getattr(self.engine, "positions", []) or []:
            mint = p.get("token")
            if not mint:
                continue
            self.positions[mint] = PositionRecord(
                mint=mint,
                amount=float(p.get("amount", 0.0) or 0.0),
                entry_price=float(p.get("entry_price", 0.0) or 0.0),
                last_price=float(p.get("last_price", p.get("entry_price", 0.0)) or 0.0),
                peak_price=float(p.get("peak_price", p.get("entry_price", 0.0)) or 0.0),
                strategy=p.get("strategy", "unassigned"),
                source=p.get("source", "unknown"),
                tx_sigs=[p.get("tx_sig", "")] if p.get("tx_sig") else [],
            )

    def apply_fill(self, fill: FillEvent) -> None:
        pos = self.positions.get(fill.mint)
        if pos is None:
            pos = PositionRecord(
                mint=fill.mint,
                amount=fill.size_sol,
                entry_price=fill.price,
                last_price=fill.price,
                peak_price=fill.price,
                strategy=fill.strategy,
                source=fill.source,
                tx_sigs=[fill.tx_sig] if fill.tx_sig else [],
            )
            self.positions[fill.mint] = pos
        else:
            total_cost = pos.entry_price * pos.amount + fill.price * fill.size_sol
            pos.amount += fill.size_sol
            if pos.amount > 0:
                pos.entry_price = total_cost / pos.amount
            pos.last_price = fill.price
            pos.peak_price = max(pos.peak_price, fill.price)
            if fill.tx_sig:
                pos.tx_sigs.append(fill.tx_sig)

        self.sync_engine_positions()

    def mark_price(self, mint: str, price: float) -> None:
        pos = self.positions.get(mint)
        if pos is None:
            return
        pos.last_price = price
        pos.peak_price = max(pos.peak_price, price)
        self.sync_engine_positions()

    def total_exposure(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def exposure_ratio(self) -> float:
        capital = max(float(getattr(self.engine, "capital", 0.0) or 0.0), 1e-9)
        return self.total_exposure() / capital

    def can_add(self, max_ratio: float = 0.7) -> bool:
        return self.exposure_ratio() < max_ratio

    def sync_engine_positions(self) -> None:
        payload = []
        for p in self.positions.values():
            payload.append({
                "token": p.mint,
                "entry_price": p.entry_price,
                "last_price": p.last_price,
                "peak_price": p.peak_price,
                "pnl_pct": p.pnl_pct,
                "amount": p.amount,
                "source": p.source,
                "strategy": p.strategy,
                "tx_sig": p.tx_sigs[-1] if p.tx_sigs else "",
            })
        self.engine.positions = payload
