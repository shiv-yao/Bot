def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _source_perf(source_stats: dict, source: str) -> float:
    row = source_stats.get(source)
    if not row:
        return 0.0

    count = float(row.get("count", 0) or 0)
    win_rate = float(row.get("win_rate", 0.0) or 0.0)
    avg_pnl = float(row.get("avg_pnl", 0.0) or 0.0)

    if count < 3:
        return 0.0

    perf = (win_rate - 0.5) * 0.6 + avg_pnl * 8.0
    return clamp(perf, -0.20, 0.20)


def get_dynamic_weights(source_stats: dict) -> dict:
    """
    初始權重：
    breakout    0.35
    smart_money 0.25
    liquidity   0.15
    insider     0.25
    """
    wb = 0.35 + _source_perf(source_stats, "breakout")
    ws = 0.25 + _source_perf(source_stats, "smart_money")
    wl = 0.15 + _source_perf(source_stats, "liquidity")
    wi = 0.25 + _source_perf(source_stats, "insider")

    wb = clamp(wb, 0.15, 0.55)
    ws = clamp(ws, 0.10, 0.45)
    wl = clamp(wl, 0.05, 0.30)
    wi = clamp(wi, 0.10, 0.40)

    total = wb + ws + wl + wi
    if total <= 0:
        return {
            "breakout": 0.35,
            "smart_money": 0.25,
            "liquidity": 0.15,
            "insider": 0.25,
        }

    return {
        "breakout": wb / total,
        "smart_money": ws / total,
        "liquidity": wl / total,
        "insider": wi / total,
    }


def combine_scores(
    breakout: float,
    smart_money: float,
    liquidity: float,
    insider: float,
    regime: str,
    source_stats: dict | None = None,
) -> float:
    weights = get_dynamic_weights(source_stats or {})

    score = (
        breakout * weights["breakout"]
        + smart_money * weights["smart_money"]
        + liquidity * weights["liquidity"]
        + insider * weights["insider"]
    )

    if regime == "trend_up":
        score *= 1.10
    elif regime == "flat":
        score *= 0.80
    elif regime == "trend_down":
        score *= 0.70
    elif regime == "volatile":
        score *= 0.90

    return min(max(score, 0.0), 1.0)
