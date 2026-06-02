from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _rate(wins: int, losses: int) -> float:
    return round(wins / max(1, wins + losses), 6)


def _bucket_net_edge(value: float) -> str:
    edge = float(value)
    if edge < 0.008:
        return "lt_0.8pct"
    if edge < 0.012:
        return "0.8_to_1.2pct"
    if edge < 0.018:
        return "1.2_to_1.8pct"
    if edge < 0.030:
        return "1.8_to_3.0pct"
    return "gte_3.0pct"


def _settled_orders(rounds: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in rounds:
        if item.get("status") != "settled":
            continue
        for raw in item.get("orders") or []:
            order = dict(raw)
            if order.get("won") is None:
                continue
            output.append(order)
    return output


def _group_stats(orders: Iterable[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    for order in orders:
        key = str(key_fn(order))
        row = grouped[key]
        if order.get("won") is True:
            row["wins"] += 1
        elif order.get("won") is False:
            row["losses"] += 1
        row["pnl"] += float(order.get("pnl") or 0.0)
    result: dict[str, dict[str, Any]] = {}
    for key, row in sorted(grouped.items()):
        wins = int(row["wins"])
        losses = int(row["losses"])
        result[key] = {
            "samples": wins + losses,
            "wins": wins,
            "losses": losses,
            "win_rate": _rate(wins, losses),
            "pnl": round(float(row["pnl"]), 8),
        }
    return result


def _recent_round_streak(rounds: Iterable[dict[str, Any]]) -> dict[str, Any]:
    settled = [item for item in rounds if item.get("status") == "settled" and item.get("won") is not None]
    settled.sort(key=lambda item: int(item.get("interval_start_ms") or 0), reverse=True)
    consecutive_losses = 0
    for item in settled:
        if item.get("won") is False:
            consecutive_losses += 1
        else:
            break
    return {
        "consecutive_round_losses": consecutive_losses,
        "last_settled_round": settled[0].get("slug") if settled else None,
    }


def build_paper_analytics(
    paper_portfolio: dict[str, Any] | None,
    *,
    cooldown_after_losses: int = 3,
    min_samples_for_review: int = 30,
) -> dict[str, Any]:
    paper = paper_portfolio or {}
    rounds = list(paper.get("closed_trades") or [])
    orders = _settled_orders(rounds)
    wins = sum(1 for order in orders if order.get("won") is True)
    losses = sum(1 for order in orders if order.get("won") is False)
    streak = _recent_round_streak(rounds)
    cooldown_recommended = streak["consecutive_round_losses"] >= max(1, int(cooldown_after_losses))

    return {
        "samples": wins + losses,
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, losses),
        "sample_status": "review_ready" if wins + losses >= max(1, int(min_samples_for_review)) else "collecting",
        "cooldown": {
            **streak,
            "threshold": max(1, int(cooldown_after_losses)),
            "recommended": cooldown_recommended,
            "reason": "consecutive_round_losses" if cooldown_recommended else None,
        },
        "by_scale_stage": _group_stats(orders, lambda order: f"stage_{int(order.get('scale_stage') or 0)}"),
        "by_net_edge": _group_stats(orders, lambda order: _bucket_net_edge(float(order.get("net_edge") or 0.0))),
        "by_direction": _group_stats(orders, lambda order: str(order.get("direction") or "UNKNOWN")),
        "by_signal_source": _group_stats(orders, lambda order: str(order.get("signal_source") or "unknown")),
        "guidance": {
            "auto_tuning_enabled": False,
            "note": "Use split metrics for review. Do not auto-tighten thresholds until enough Paper samples exist.",
        },
    }
