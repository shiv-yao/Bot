from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI


def register_history_routes(app: FastAPI, settings: Any, portfolio: Any) -> None:
    @app.get("/history/status")
    async def history_status() -> dict[str, Any]:
        db_path = str(portfolio.store.db_path)
        name = Path(db_path).name
        return {
            "mode": "paper" if not settings.live_enabled else "live",
            "profile": "balanced_btc5m_hf" if not settings.live_enabled else "live_baseline",
            "database": {
                "path": db_path,
                "filename": name,
                "is_btc5m_isolated": name == "polymarket_paper_btc5m_balanced.db",
                "legacy_database_preserved": "/data/polymarket_paper.db",
            },
            "market": {
                "asset": "BTC",
                "interval_sec": int(settings.market_interval_sec),
                "interval_minutes": round(float(settings.market_interval_sec) / 60, 2),
                "slug_prefix": str(settings.market_slug_prefix),
            },
            "safety": {
                "live_enabled": bool(settings.live_enabled),
                "paper_high_frequency_profile": bool(settings.paper_high_frequency_profile),
            },
        }
