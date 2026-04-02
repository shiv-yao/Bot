# app/alpha/alpha_learner.py

from collections import defaultdict

def compute_alpha_edge(trade_history):
    alpha_perf = defaultdict(lambda: {
        "count": 0,
        "total_pnl": 0.0,
        "avg_pnl": 0.0
    })

    for t in trade_history:
        meta = t.get("meta", {}) or {}
        pnl = float(t.get("pnl", 0.0) or 0.0)

        for key in ["breakout", "smart_money", "liquidity", "momentum", "insider"]:
            score = float(meta.get(key, 0.0) or 0.0)

            if score > 0:
                alpha_perf[key]["count"] += 1
                alpha_perf[key]["total_pnl"] += pnl * score

    for k, v in alpha_perf.items():
        if v["count"] > 0:
            v["avg_pnl"] = v["total_pnl"] / v["count"]

    return dict(alpha_perf)
