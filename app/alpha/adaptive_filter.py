def clamp(v, lo, hi):
    return max(lo, min(v, hi))


def compute_market_state(metrics: dict):
    if not metrics:
        return "neutral"

    perf = metrics.get("performance", {})
    win_rate = perf.get("win_rate", 0)
    pf = perf.get("profit_factor", 0)
    dd = metrics.get("summary", {}).get("drawdown", 0)

    if win_rate > 0.6 and pf > 1.5:
        return "aggressive"

    if win_rate < 0.45 or dd < -0.1:
        return "defensive"

    return "neutral"


def adaptive_thresholds(metrics):
    state = compute_market_state(metrics)

    if state == "aggressive":
        return {
            "wallet_min": 1,
            "liquidity_min": 0.003,
            "max_price_impact": 0.08,
            "score_boost": 1.1,
        }

    elif state == "defensive":
        return {
            "wallet_min": 3,
            "liquidity_min": 0.02,
            "max_price_impact": 0.02,
            "score_boost": 0.8,
        }

    return {
        "wallet_min": 2,
        "liquidity_min": 0.005,
        "max_price_impact": 0.05,
        "score_boost": 1.0,
    }


def adaptive_filter(features: dict, metrics: dict):
    th = adaptive_thresholds(metrics)

    if features["wallet_count"] < th["wallet_min"]:
        return False, th

    if features["liquidity"] < th["liquidity_min"]:
        return False, th

    if features["price_impact"] > th["max_price_impact"]:
        return False, th

    return True, th
