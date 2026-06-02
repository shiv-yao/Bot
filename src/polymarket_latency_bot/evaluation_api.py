from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def _empty_summary(path: str, exists: bool) -> dict[str, Any]:
    return {
        "db_path": path,
        "exists": exists,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "flat": 0,
        "win_rate": 0.0,
        "net_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
    }


def _read_summary(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return _empty_summary(path, False)
    try:
        with sqlite3.connect(path, timeout=2) as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS closed_trades,
                    COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END), 0) AS losses,
                    COALESCE(SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END), 0) AS flat,
                    COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                    COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
                    ABS(COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0)) AS gross_loss
                FROM paper_trades
                """
            ).fetchone()
    except (sqlite3.Error, OSError):
        return _empty_summary(path, True)
    if row is None:
        return _empty_summary(path, True)
    closed = int(row[0] or 0)
    wins = int(row[1] or 0)
    losses = int(row[2] or 0)
    flat = int(row[3] or 0)
    net = float(row[4] or 0.0)
    gross_profit = float(row[5] or 0.0)
    gross_loss = float(row[6] or 0.0)
    return {
        "db_path": path,
        "exists": True,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": round(wins / max(1, wins + losses), 6),
        "net_pnl": round(net, 8),
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def register_evaluation_routes(app: FastAPI, portfolio: Any) -> None:
    legacy_path = "/data/polymarket_paper.db"

    @app.get("/performance/compare")
    async def performance_compare() -> dict[str, Any]:
        current = _read_summary(str(portfolio.store.db_path))
        legacy = _read_summary(legacy_path)
        return {
            "current_profile": "balanced_btc5m_hf",
            "current": current,
            "legacy_15m_history": legacy,
            "note": "The two databases are reported separately and are never merged.",
        }

    @app.get("/evaluation/status")
    async def evaluation_status() -> dict[str, Any]:
        current = _read_summary(str(portfolio.store.db_path))
        closed = int(current["closed_trades"])
        if closed < 100:
            stage = "collecting_initial_sample"
            recommendation = "Keep Paper mode running. Do not judge the strategy yet."
        elif closed < 500:
            stage = "early_evaluation"
            recommendation = "Review win rate, profit factor, drawdown and exit reasons before tuning again."
        else:
            stage = "evaluation_ready"
            recommendation = "Use the isolated 5m report for parameter review. Live mode should remain disabled until manual validation is complete."
        return {
            "profile": "balanced_btc5m_hf",
            "stage": stage,
            "minimum_initial_sample": 100,
            "preferred_review_sample": 500,
            "current": current,
            "recommendation": recommendation,
        }
