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

    # 讓績效映射到一個溫和分數
    # win_rate 0.5 視為中性；avg_pnl 正負作微調
    perf = (win_rate - 0.5) * 0.6 + avg_pnl * 8.0
    return clamp(perf, -0.20, 0.20)


def get_dynamic_weights(source_stats: dict) -> dict:
    """
    初始權重：
    breakout    0.50
    smart_money 0.30
    liquidity   0.20

    根據近期 source 績效微調，但不讓權重崩掉。
    """
    wb = 0.50 + _source_perf(source_stats, "breakout")
    ws = 0.30 + _source_perf(source_stats, "smart_money")
    wl = 0.20 + _source_perf(source_stats, "liquidity")

    # 權重上下限
    wb = clamp(wb, 0.20, 0.65)
    ws = clamp(ws, 0.15, 0.55)
    wl = clamp(wl, 0.10, 0.40)

    total = wb + ws + wl
    if total <= 0:
        return {
            "breakout": 0.50,
            "smart_money": 0.30,
            "liquidity": 0.20,
        }

    return {
        "breakout": wb / total,
        "smart_money": ws / total,
        "liquidity": wl / total,
    }


def combine_scores(
    breakout: float,
    smart_money: float,
    liquidity: float,
    regime: str,
    source_stats: dict | None = None,
) -> float:
    """
    三策略融合 + 自動調權
    """
    weights = get_dynamic_weights(source_stats or {})

    score = (
        breakout * weights["breakout"]
        + smart_money * weights["smart_money"]
        + liquidity * weights["liquidity"]
    )

    # regime 調整
    if regime == "trend_up":
        score *= 1.08
    elif regime == "flat":
        score *= 0.82
    elif regime == "trend_down":
        score *= 0.72
    elif regime == "volatile":
        score *= 0.92

    return min(score, 1.0)
