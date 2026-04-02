def clamp(v, lo, hi):
    return max(lo, min(v, hi))


def compute_market_state(metrics: dict | None):
    if not metrics:
        return "neutral"

    perf = metrics.get("performance", {}) or {}
    summary = metrics.get("summary", {}) or {}

    win_rate = float(perf.get("win_rate", 0) or 0)
    pf = float(perf.get("profit_factor", 0) or 0)
    dd = float(summary.get("drawdown", 0) or 0)

    if win_rate > 0.60 and pf > 1.50:
        return "aggressive"

    if win_rate < 0.45 or dd < -0.10:
        return "defensive"

    return "neutral"


def adaptive_thresholds(metrics: dict | None):
    state = compute_market_state(metrics)

    if state == "aggressive":
        return {
            "wallet_min": 1,
            "liquidity_min": 0.003,
            "max_price_impact": 0.08,
            "score_boost": 1.10,
            "state": state,
        }

    if state == "defensive":
        return {
            "wallet_min": 3,
            "liquidity_min": 0.020,
            "max_price_impact": 0.020,
            "score_boost": 0.80,
            "state": state,
        }

    return {
        "wallet_min": 2,
        "liquidity_min": 0.005,
        "max_price_impact": 0.050,
        "score_boost": 1.00,
        "state": state,
    }


def adaptive_filter(features: dict | None, metrics: dict | None):
    if not features:
        return False, {
            "wallet_min": 999,
            "liquidity_min": 999,
            "max_price_impact": 999,
            "score_boost": 0.0,
            "state": "invalid",
        }

    th = adaptive_thresholds(metrics)

    if float(features.get("wallet_count", 0) or 0) < th["wallet_min"]:
        return False, th

    if float(features.get("liquidity", 0) or 0) < th["liquidity_min"]:
        return False, th

    if float(features.get("price_impact", 999) or 999) > th["max_price_impact"]:
        return False, th

    return True, th
