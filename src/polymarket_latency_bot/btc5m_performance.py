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


def _brier_score(orders: Iterable[dict[str, Any]]) -> float | None:
    squared_errors: list[float] = []
    for order in orders:
        if order.get("expected_probability") is None or order.get("won") is None:
            continue
        probability = min(1.0, max(0.0, float(order.get("expected_probability") or 0.0)))
        outcome = 1.0 if order.get("won") is True else 0.0
        squared_errors.append((probability - outcome) ** 2)
    return round(sum(squared_errors) / len(squared_errors), 8) if squared_errors else None


def _calibration_stats(
    orders: Iterable[dict[str, Any]],
    *,
    min_bucket_samples: int,
    overconfidence_gap_threshold: float,
) -> dict[str, Any]:
    eligible = [order for order in orders if order.get("expected_probability") is not None and order.get("won") is not None]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    predicted_total = 0.0
    actual_total = 0.0
    for order in eligible:
        probability = min(1.0, max(0.0, float(order.get("expected_probability") or 0.0)))
        outcome = 1.0 if order.get("won") is True else 0.0
        predicted_total += probability
        actual_total += outcome
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
            overconfidence_reviews.append({"bucket": key, **row, "action": "review_only", "reason": "overconfidence_gap"})

    samples = len(eligible)
    return {
        "samples": samples,
        "brier_score": _brier_score(eligible),
        "average_expected_probability": round(predicted_total / samples, 6) if samples else None,
        "observed_win_rate": round(actual_total / samples, 6) if samples else None,
        "overconfidence_gap_threshold": round(float(overconfidence_gap_threshold), 6),
        "min_bucket_samples": max(1, int(min_bucket_samples)),
        "buckets": buckets,
        "overconfidence_reviews": overconfidence_reviews,
    }


def _drift_stats(
    orders: Iterable[dict[str, Any]],
    *,
    rolling_window: int,
    min_samples: int,
    win_rate_drop_threshold: float,
    brier_increase_threshold: float,
) -> dict[str, Any]:
    ordered = sorted(orders, key=lambda order: int(order.get("created_ms") or 0))
    window = max(1, int(rolling_window))
    recent = ordered[-window:]
    overall_wins = sum(1 for order in ordered if order.get("won") is True)
    overall_losses = sum(1 for order in ordered if order.get("won") is False)
    recent_wins = sum(1 for order in recent if order.get("won") is True)
    recent_losses = sum(1 for order in recent if order.get("won") is False)
    overall_win_rate = _rate(overall_wins, overall_losses)
    recent_win_rate = _rate(recent_wins, recent_losses)
    win_rate_drop = round(overall_win_rate - recent_win_rate, 6)
    overall_brier = _brier_score(ordered)
    recent_brier = _brier_score(recent)
    brier_increase = round(float(recent_brier) - float(overall_brier), 8) if overall_brier is not None and recent_brier is not None else None
    reasons: list[str] = []
    enough_samples = len(recent) >= max(1, int(min_samples))
    if enough_samples and win_rate_drop > float(win_rate_drop_threshold):
        reasons.append("rolling_win_rate_deterioration")
    if enough_samples and brier_increase is not None and brier_increase > float(brier_increase_threshold):
        reasons.append("rolling_brier_deterioration")
    return {
        "window": window,
        "samples": len(recent),
        "min_samples": max(1, int(min_samples)),
        "overall_win_rate": overall_win_rate,
        "rolling_win_rate": recent_win_rate,
        "win_rate_drop": win_rate_drop,
        "win_rate_drop_threshold": round(float(win_rate_drop_threshold), 6),
        "overall_brier_score": overall_brier,
        "rolling_brier_score": recent_brier,
        "brier_increase": brier_increase,
        "brier_increase_threshold": round(float(brier_increase_threshold), 6),
        "review_only": bool(reasons),
        "reasons": reasons,
    }


def _walk_forward_stats(
    orders: Iterable[dict[str, Any]],
    *,
    train_min_samples: int,
    validation_samples: int,
    win_rate_drop_threshold: float,
    brier_increase_threshold: float,
) -> dict[str, Any]:
    ordered = sorted(orders, key=lambda order: int(order.get("created_ms") or 0))
    validation_count = max(1, int(validation_samples))
    baseline = ordered[:-validation_count] if len(ordered) > validation_count else []
    validation = ordered[-validation_count:] if len(ordered) >= validation_count else ordered
    required_train = max(1, int(train_min_samples))
    collecting = len(baseline) < required_train or len(validation) < validation_count
    baseline_wins = sum(1 for order in baseline if order.get("won") is True)
    baseline_losses = sum(1 for order in baseline if order.get("won") is False)
    validation_wins = sum(1 for order in validation if order.get("won") is True)
    validation_losses = sum(1 for order in validation if order.get("won") is False)
    baseline_win_rate = _rate(baseline_wins, baseline_losses)
    validation_win_rate = _rate(validation_wins, validation_losses)
    win_rate_delta = round(validation_win_rate - baseline_win_rate, 6)
    baseline_brier = _brier_score(baseline)
    validation_brier = _brier_score(validation)
    brier_delta = round(float(validation_brier) - float(baseline_brier), 8) if baseline_brier is not None and validation_brier is not None else None
    reasons: list[str] = []
    if not collecting and -win_rate_delta > float(win_rate_drop_threshold):
        reasons.append("validation_win_rate_below_baseline")
    if not collecting and brier_delta is not None and brier_delta > float(brier_increase_threshold):
        reasons.append("validation_brier_worse_than_baseline")
    return {
        "status": "collecting" if collecting else "ready",
        "baseline_samples": len(baseline),
        "validation_samples": len(validation),
        "required_train_samples": required_train,
        "required_validation_samples": validation_count,
        "baseline_win_rate": baseline_win_rate,
        "validation_win_rate": validation_win_rate,
        "win_rate_delta": win_rate_delta,
        "win_rate_drop_threshold": round(float(win_rate_drop_threshold), 6),
        "baseline_brier_score": baseline_brier,
        "validation_brier_score": validation_brier,
        "brier_delta": brier_delta,
        "brier_increase_threshold": round(float(brier_increase_threshold), 6),
        "review_only": bool(reasons),
        "reasons": reasons,
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
    drift_min_samples: int = 20,
    drift_win_rate_drop_threshold: float = 0.15,
    drift_brier_increase_threshold: float = 0.10,
    walk_forward_train_min_samples: int = 30,
    walk_forward_validation_samples: int = 20,
    walk_forward_win_rate_drop_threshold: float = 0.10,
    walk_forward_brier_increase_threshold: float = 0.05,
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
    calibration = _calibration_stats(orders, min_bucket_samples=calibration_min_bucket_samples, overconfidence_gap_threshold=overconfidence_gap_threshold)
    drift = _drift_stats(orders, rolling_window=rolling_window, min_samples=drift_min_samples, win_rate_drop_threshold=drift_win_rate_drop_threshold, brier_increase_threshold=drift_brier_increase_threshold)
    walk_forward = _walk_forward_stats(orders, train_min_samples=walk_forward_train_min_samples, validation_samples=walk_forward_validation_samples, win_rate_drop_threshold=walk_forward_win_rate_drop_threshold, brier_increase_threshold=walk_forward_brier_increase_threshold)
    return {
        "samples": wins + losses,
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, losses),
        "sample_status": "review_ready" if wins + losses >= max(1, int(min_samples_for_review)) else "collecting",
        "rolling": _rolling_stats(orders, rolling_window),
        "calibration": calibration,
        "drift": drift,
        "walk_forward": walk_forward,
        "cooldown": {**streak, "threshold": max(1, int(cooldown_after_losses)), "recommended": cooldown_recommended, "reason": "consecutive_round_losses" if cooldown_recommended else None},
        "by_scale_stage": by_scale_stage,
        "by_net_edge": by_net_edge,
        "by_direction": by_direction,
        "by_signal_source": by_signal_source,
        "review": {
            "min_group_samples": min_group_samples,
            "win_rate_threshold": threshold,
            "recommendation_count": review_count + len(calibration["overconfidence_reviews"]) + (1 if drift["review_only"] else 0) + (1 if walk_forward["review_only"] else 0),
            "recommendations": review_recommendations,
            "overconfidence": calibration["overconfidence_reviews"],
            "drift": drift if drift["review_only"] else None,
            "walk_forward": walk_forward if walk_forward["review_only"] else None,
        },
        "guidance": {"auto_tuning_enabled": False, "note": "Recommendations are observational only. Review split metrics, calibration, drift and walk-forward validation before changing Paper thresholds."},
    }
