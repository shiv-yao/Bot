def adaptive_filter(features, metrics, no_trade_cycles=0):
    """
    return:
        ok: bool
        th: dict
    """

    # ===== default =====
    state = "neutral"
    score_min = 0.20
    score_boost = 1.0

    # ===== metrics-based regime =====
    if metrics and isinstance(metrics, dict):
        perf = metrics.get("performance", {})
        summary = metrics.get("summary", {})

        win_rate = float(perf.get("win_rate", 0.0))
        pf = float(perf.get("profit_factor", 1.0))
        dd = float(summary.get("drawdown", 0.0))

        # ===== GOOD REGIME =====
        if win_rate > 0.55 and pf > 1.2:
            state = "aggressive"
            score_min = 0.15
            score_boost = 1.2

        # ===== BAD REGIME =====
        elif win_rate < 0.35 or pf < 0.8 or dd < -0.2:
            state = "defensive"
            score_min = 0.28
            score_boost = 0.85

    # ===== NO TRADE RELAX =====
    loosen_factor = 1.0

    if no_trade_cycles > 3:
        loosen_factor *= 0.9
        state += "_loosen1"

    if no_trade_cycles > 6:
        loosen_factor *= 0.8
        state += "_loosen2"

    if no_trade_cycles > 10:
        loosen_factor *= 0.7
        state += "_loosen3"

    score_min *= loosen_factor

    # ===== feature checks =====
    wallet_ok = features.get("wallet_count", 0) >= 2
    liquidity_ok = features.get("liquidity", 0) > 0.0005
    impact_ok = features.get("price_impact", 1) < 0.03

    # ===== HARD FILTER =====
    if not liquidity_ok or not impact_ok:
        return False, {
            "state": state,
            "score_min": score_min,
            "score_boost": score_boost,
        }

    # ===== SOFT FILTER =====
    if wallet_ok:
        return True, {
            "state": state,
            "score_min": score_min,
            "score_boost": score_boost,
        }

    # ===== fallback allow =====
    if no_trade_cycles > 5:
        return True, {
            "state": state + "_force",
            "score_min": score_min,
            "score_boost": score_boost,
        }

    return False, {
        "state": state,
        "score_min": score_min,
        "score_boost": score_boost,
    }
