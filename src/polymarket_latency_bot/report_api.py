from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response


def register_report_routes(app: FastAPI, portfolio: Any) -> None:
    @app.get("/report/daily")
    async def daily_report() -> dict[str, Any]:
        with portfolio.store._connect() as db:
            rows = db.execute(
                """
                SELECT
                    DATE(closed_ms / 1000, 'unixepoch') AS day,
                    COUNT(*) AS trades,
                    ROUND(COALESCE(SUM(realized_pnl), 0), 8) AS net_pnl,
                    ROUND(COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0), 8) AS gross_profit,
                    ROUND(ABS(COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0)), 8) AS gross_loss,
                    ROUND(AVG(hold_ms), 2) AS average_hold_ms,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses
                FROM paper_trades
                GROUP BY day
                ORDER BY day DESC
                LIMIT 90
                """
            ).fetchall()
        return {"days": [dict(row) for row in rows], "db_path": portfolio.store.db_path}

    @app.get("/report/exit-reasons")
    async def exit_reason_report() -> dict[str, Any]:
        with portfolio.store._connect() as db:
            rows = db.execute(
                """
                SELECT
                    close_reason,
                    COUNT(*) AS trades,
                    ROUND(COALESCE(SUM(realized_pnl), 0), 8) AS net_pnl,
                    ROUND(AVG(realized_pnl), 8) AS average_trade_pnl,
                    ROUND(AVG(hold_ms), 2) AS average_hold_ms,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses
                FROM paper_trades
                GROUP BY close_reason
                ORDER BY trades DESC
                """
            ).fetchall()
        return {"exit_reasons": [dict(row) for row in rows], "db_path": portfolio.store.db_path}

    @app.get("/export/trades.csv")
    async def export_trades_csv() -> Response:
        with portfolio.store._connect() as db:
            rows = db.execute(
                """
                SELECT id, token_id, direction, notional_usd, entry_price, exit_price,
                       shares, realized_pnl, opened_ms, closed_ms, hold_ms,
                       close_reason, market_slug
                FROM paper_trades
                ORDER BY id DESC
                """
            ).fetchall()
        buffer = io.StringIO()
        fieldnames = [
            "id", "token_id", "direction", "notional_usd", "entry_price", "exit_price",
            "shares", "realized_pnl", "opened_ms", "closed_ms", "hold_ms", "close_reason",
            "market_slug",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=paper_trades.csv"},
        )
