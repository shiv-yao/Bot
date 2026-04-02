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


def adaptive_thresholds(metrics: dict | None, no_trade_cycles: int = 0):
    state = compute_market_state(metrics)

    if state == "aggressive":
        th = {
            "wallet_min": 1,
            "liquidity_min": 0.003,
            "max_price_impact": 0.08,
            "score_boost": 1.10,
            "score_min": 0.15,
            "state": state,
        }
    elif state == "defensive":
        th = {
            "wallet_min": 3,
            "liquidity_min": 0.020,
            "max_price_impact": 0.020,
            "score_boost": 0.80,
            "score_min": 0.25,
            "state": state,
        }
    else:
        th = {
            "wallet_min": 2,
            "liquidity_min": 0.005,
            "max_price_impact": 0.050,
            "score_boost": 1.00,
            "score_min": 0.20,
            "state": state,
        }

    # 長時間沒交易，自動放寬
    if no_trade_cycles >= 5:
        th["wallet_min"] = max(1, th["wallet_min"] - 1)
        th["liquidity_min"] *= 0.6
        th["max_price_impact"] *= 1.5
        th["score_min"] *= 0.8
        th["state"] = f"{th['state']}_loosen1"

    if no_trade_cycles >= 10:
        th["wallet_min"] = 1
        th["liquidity_min"] *= 0.5
        th["max_price_impact"] *= 1.5
        th["score_min"] *= 0.8
        th["state"] = f"{th['state']}_loosen2"

    return th


def adaptive_filter(features: dict | None, metrics: dict | None, no_trade_cycles: int = 0):
    if not features:
        return False, {
            "wallet_min": 999,
            "liquidity_min": 999,
            "max_price_impact": 999,
            "score_boost": 0.0,
            "score_min": 999,
            "state": "invalid",
        }

    th = adaptive_thresholds(metrics, no_trade_cycles=no_trade_cycles)

    if float(features.get("wallet_count", 0) or 0) < th["wallet_min"]:
        return False, th

    if float(features.get("liquidity", 0) or 0) < th["liquidity_min"]:
        return False, th

    if float(features.get("price_impact", 999) or 999) > th["max_price_impact"]:
        return False, th

    return True, th
