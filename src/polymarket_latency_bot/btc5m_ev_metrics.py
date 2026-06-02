from __future__ import annotations

from typing import Any, Iterable


def _settled_orders(paper_portfolio: dict[str, Any] | None) -> list[dict[str, Any]]:
    paper = paper_portfolio or {}
    rounds = list(paper.get("closed_trades") or [])
    orders: list[dict[str, Any]] = []
    for item in rounds:
        if item.get("status") != "settled":
            continue
        for raw in item.get("orders") or []:
            order = dict(raw)
            if order.get("won") is None:
                continue
            orders.append(order)
    orders.sort(key=lambda order: int(order.get("created_ms") or 0))
    return orders


def _safe_average(values: Iterable[float]) -> float | None:
    rows = list(values)
    return round(sum(rows) / len(rows), 8) if rows else None


def build_ev_metrics(paper_portfolio: dict[str, Any] | None) -> dict[str, Any]:
    """Return realized and expected-value diagnostics for settled Paper orders.

    The metrics are observational only. They never alter thresholds, pause the
    strategy or create additional Paper positions.
    """

    orders = _settled_orders(paper_portfolio)
    total_notional = sum(float(order.get("notional_usd") or 0.0) for order in orders)
    realized_pnl = sum(float(order.get("pnl") or 0.0) for order in orders)
    expected_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    wins: list[float] = []
    losses: list[float] = []

    for order in orders:
        entry_price = float(order.get("entry_price") or 0.0)
        shares = float(order.get("shares") or 0.0)
        expected_probability = float(order.get("expected_probability") or 0.0)
        pnl = float(order.get("pnl") or 0.0)
        expected_pnl += shares * (expected_probability - entry_price)
        if pnl > 0:
            gross_profit += pnl
            wins.append(pnl)
        elif pnl < 0:
            gross_loss += abs(pnl)
            losses.append(pnl)
        cumulative_pnl += pnl
        peak_pnl = max(peak_pnl, cumulative_pnl)
        max_drawdown = max(max_drawdown, peak_pnl - cumulative_pnl)

    profit_factor = round(gross_profit / gross_loss, 8) if gross_loss > 0 else (None if gross_profit == 0 else "infinite")
    average_entry_price = _safe_average(float(order.get("entry_price") or 0.0) for order in orders)
    average_net_edge = _safe_average(float(order.get("net_edge") or 0.0) for order in orders)
    realized_ev = round(realized_pnl / total_notional, 8) if total_notional > 0 else None
    expected_ev = round(expected_pnl / total_notional, 8) if total_notional > 0 else None
    ev_calibration_gap = round(realized_ev - expected_ev, 8) if realized_ev is not None and expected_ev is not None else None

    return {
        "samples": len(orders),
        "total_notional_usd": round(total_notional, 8),
        "realized_pnl": round(realized_pnl, 8),
        "expected_pnl": round(expected_pnl, 8),
        "realized_ev": realized_ev,
        "expected_ev": expected_ev,
        "ev_calibration_gap": ev_calibration_gap,
        "average_entry_price": average_entry_price,
        "average_net_edge": average_net_edge,
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": profit_factor,
        "average_win": _safe_average(wins),
        "average_loss": _safe_average(losses),
        "maximum_drawdown": round(max_drawdown, 8),
        "note": "Observational only. EV metrics never change Paper thresholds automatically.",
    }
