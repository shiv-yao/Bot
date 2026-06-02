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


def _bucket_probability(value: float) -> str:
    probability = min(0.999999, max(0.0, float(value)))
    lower = int(probability * 20) * 5
    upper = min(100, lower + 5)
    return f"{lower:02d}_to_{upper:02d}pct"


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


def _review_groups(
    groups: dict[str, dict[str, Any]],
    *,
    min_samples: int,
    min_win_rate: float,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for key, row in sorted(groups.items()):
        samples = int(row.get("samples") or 0)
        win_rate = float(row.get("win_rate") or 0.0)
        pnl = float(row.get("pnl") or 0.0)
        if samples < min_samples:
            continue
        reasons: list[str] = []
        if win_rate < min_win_rate:
            reasons.append("win_rate_below_review_threshold")
        if pnl < 0:
            reasons.append("negative_pnl")
        if reasons:
            recommendations.append({
                "group": key,
                "samples": samples,
                "win_rate": win_rate,
                "pnl": round(pnl, 8),
                "action": "review_only",
                "reasons": reasons,
            })
    return recommendations


def _rolling_stats(orders: Iterable[dict[str, Any]], window: int) -> dict[str, Any]:
    ordered = sorted(orders, key=lambda order: int(order.get("created_ms") or 0))
    recent = ordered[-max(1, int(window)):]
    wins = sum(1 for order in recent if order.get("won") is True)
    losses = sum(1 for order in recent if order.get("won") is False)
    return {
        "window": max(1, int(window)),
        "samples": wins + losses,
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, losses),
        "pnl": round(sum(float(order.get("pnl") or 0.0) for order in recent), 8),
    }


def _calibration_stats(
    orders: Iterable[dict[str, Any]],
    *,
    min_bucket_samples: int,
    overconfidence_gap_threshold: float,
) -> dict[str, Any]:
    eligible = [
        order for order in orders
        if order.get("expected_probability") is not None and order.get("won") is not None
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    squared_errors: list[float] = []
    predicted_total = 0.0
    actual_total = 0.0
    for order in eligible:
        probability = min(1.0, max(0.0, float(order.get("expected_probability") or 0.0)))
        outcome = 1.0 if order.get("won") is True else 0.0
        predicted_total += probability
        actual_total += outcome
        squared_errors.append((probability - outcome) ** 2)
        grouped[_bucket_probability(probability)].append(order)

    buckets: dict[str, dict[str, Any]] = {}
    overconfidence_reviews: list[dict[str, Any]] = []
    for key, items in sorted(grouped.items()):
        samples = len(items)
        average_probability = sum(float(item.get("expected_probability") or 0.0) for item in items) / samples
        wins = sum(1 for item in items if item.get("won") is True)
        observed_win_rate = wins / samples
        calibration_gap = average_probability - observed_win_rate
        row = {
            "samples": samples,
            "average_expected_probability": round(average_probability, 6),
            "observed_win_rate": round(observed_win_rate, 6),
            "calibration_gap": round(calibration_gap, 6),
        }
        buckets[key] = row
        if samples >= max(1, int(min_bucket_samples)) and calibration_gap > float(overconfidence_gap_threshold):
            overconfidence_reviews.append({
                "bucket": key,
                **row,
                "action": "review_only",
                "reason": "overconfidence_gap",
            })

    samples = len(eligible)
    return {
        "samples": samples,
        "brier_score": round(sum(squared_errors) / samples, 8) if samples else None,
        "average_expected_probability": round(predicted_total / samples, 6) if samples else None,
        "observed_win_rate": round(actual_total / samples, 6) if samples else None,
        "overconfidence_gap_threshold": round(float(overconfidence_gap_threshold), 6),
        "min_bucket_samples": max(1, int(min_bucket_samples)),
        "buckets": buckets,
        "overconfidence_reviews": overconfidence_reviews,
    }


def build_paper_analytics(
    paper_portfolio: dict[str, Any] | None,
    *,
    cooldown_after_losses: int = 3,
    min_samples_for_review: int = 30,
    min_group_samples_for_review: int = 10,
    review_win_rate_threshold: float = 0.45,
    rolling_window: int = 30,
    calibration_min_bucket_samples: int = 10,
    overconfidence_gap_threshold: float = 0.10,
) -> dict[str, Any]:
    paper = paper_portfolio or {}
    rounds = list(paper.get("closed_trades") or [])
    orders = _settled_orders(rounds)
    wins = sum(1 for order in orders if order.get("won") is True)
    losses = sum(1 for order in orders if order.get("won") is False)
    streak = _recent_round_streak(rounds)
    cooldown_recommended = streak["consecutive_round_losses"] >= max(1, int(cooldown_after_losses))
    by_scale_stage = _group_stats(orders, lambda order: f"stage_{int(order.get('scale_stage') or 0)}")
    by_net_edge = _group_stats(orders, lambda order: _bucket_net_edge(float(order.get("net_edge") or 0.0)))
    by_direction = _group_stats(orders, lambda order: str(order.get("direction") or "UNKNOWN"))
    by_signal_source = _group_stats(orders, lambda order: str(order.get("signal_source") or "unknown"))
    min_group_samples = max(1, int(min_group_samples_for_review))
    threshold = min(1.0, max(0.0, float(review_win_rate_threshold)))

    review_recommendations = {
        "scale_stage": _review_groups(by_scale_stage, min_samples=min_group_samples, min_win_rate=threshold),
        "net_edge": _review_groups(by_net_edge, min_samples=min_group_samples, min_win_rate=threshold),
        "direction": _review_groups(by_direction, min_samples=min_group_samples, min_win_rate=threshold),
        "signal_source": _review_groups(by_signal_source, min_samples=min_group_samples, min_win_rate=threshold),
    }
    review_count = sum(len(items) for items in review_recommendations.values())
    calibration = _calibration_stats(
        orders,
        min_bucket_samples=calibration_min_bucket_samples,
        overconfidence_gap_threshold=overconfidence_gap_threshold,
    )

    return {
        "samples": wins + losses,
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, losses),
        "sample_status": "review_ready" if wins + losses >= max(1, int(min_samples_for_review)) else "collecting",
        "rolling": _rolling_stats(orders, rolling_window),
        "calibration": calibration,
        "cooldown": {
            **streak,
            "threshold": max(1, int(cooldown_after_losses)),
            "recommended": cooldown_recommended,
            "reason": "consecutive_round_losses" if cooldown_recommended else None,
        },
        "by_scale_stage": by_scale_stage,
        "by_net_edge": by_net_edge,
        "by_direction": by_direction,
        "by_signal_source": by_signal_source,
        "review": {
            "min_group_samples": min_group_samples,
            "win_rate_threshold": threshold,
            "recommendation_count": review_count + len(calibration["overconfidence_reviews"]),
            "recommendations": review_recommendations,
            "overconfidence": calibration["overconfidence_reviews"],
        },
        "guidance": {
            "auto_tuning_enabled": False,
            "note": "Recommendations are observational only. Review split metrics and calibration before changing Paper thresholds.",
        },
    }
