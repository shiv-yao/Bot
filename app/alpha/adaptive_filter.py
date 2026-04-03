def compute_market_state(metrics: dict | None):
    if not metrics:
        return "neutral"

    perf = metrics.get("performance", {}) or {}
    summary = metrics.get("summary", {}) or {}

    win_rate = float(perf.get("win_rate", 0) or 0)
    pf = float(perf.get("profit_factor", 0) or 0)
    dd = float(summary.get("drawdown", 0) or 0)

    # ⭐ aggressive：只有真的健康才進
    if win_rate > 0.62 and pf > 1.60 and dd > -0.08:
        return "aggressive"

    # ⭐ defensive：只要 DD 偏深或品質下降就進防守
    if win_rate < 0.48 or pf < 1.05 or dd < -0.12:
        return "defensive"

    return "neutral"


def adaptive_thresholds(metrics: dict | None, no_trade_cycles: int = 0):
    state = compute_market_state(metrics)

    if state == "aggressive":
        th = {
            "wallet_min": 2,
            "liquidity_min": 0.006,
            "max_price_impact": 0.060,
            "score_boost": 1.05,
            "score_min": 0.18,
            "state": state,
        }

    elif state == "defensive":
        th = {
            "wallet_min": 3,
            "liquidity_min": 0.015,
            "max_price_impact": 0.025,
            "score_boost": 0.90,
            "score_min": 0.24,
            "state": state,
        }

    else:
        th = {
            "wallet_min": 2,
            "liquidity_min": 0.008,
            "max_price_impact": 0.040,
            "score_boost": 1.00,
            "score_min": 0.20,
            "state": state,
        }

    # ===== 平滑放寬，不一次放太多 =====
    if no_trade_cycles >= 4:
        th["wallet_min"] = max(1, th["wallet_min"] - 1)
        th["liquidity_min"] *= 0.85
        th["max_price_impact"] *= 1.15
        th["score_min"] *= 0.92
        th["state"] = f"{th['state']}_loosen1"

    if no_trade_cycles >= 8:
        th["liquidity_min"] *= 0.85
        th["max_price_impact"] *= 1.12
        th["score_min"] *= 0.94
        th["state"] = f"{th['state']}_loosen2"

    if no_trade_cycles >= 12:
        th["wallet_min"] = 1
        th["liquidity_min"] *= 0.85
        th["max_price_impact"] *= 1.10
        th["score_min"] *= 0.95
        th["state"] = f"{th['state']}_loosen3"

    # ===== 安全上下限 =====
    th["wallet_min"] = max(1, int(th["wallet_min"]))
    th["liquidity_min"] = max(0.0015, float(th["liquidity_min"]))
    th["max_price_impact"] = min(max(0.01, float(th["max_price_impact"])), 0.12)
    th["score_boost"] = min(max(0.75, float(th["score_boost"])), 1.15)
    th["score_min"] = min(max(0.10, float(th["score_min"])), 0.30)

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

    wallet_count = float(features.get("wallet_count", 0) or 0)
    liquidity = float(features.get("liquidity", 0) or 0)
    price_impact = float(features.get("price_impact", 999) or 999)

    if wallet_count < th["wallet_min"]:
        return False, th

    if liquidity < th["liquidity_min"]:
        return False, th

    if price_impact > th["max_price_impact"]:
        return False, th

    return True, th
