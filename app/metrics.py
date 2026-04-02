import math
from statistics import mean

def compute_metrics(engine):
    trades = engine.trade_history

    if not trades:
        return {}

    pnls = [t["pnl"] for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_return = sum(pnls)

    win_rate = len(wins) / len(pnls)

    avg_win = mean(wins) if wins else 0
    avg_loss = mean(losses) if losses else 0

    profit_factor = (
        abs(sum(wins) / sum(losses))
        if losses and sum(losses) != 0
        else 999
    )

    # ===== equity curve =====
    equity = [1]
    for p in pnls:
        equity.append(equity[-1] * (1 + p))

    # ===== max drawdown =====
    peak = equity[0]
    max_dd = 0

    for x in equity:
        if x > peak:
            peak = x
        dd = (x - peak) / peak
        if dd < max_dd:
            max_dd = dd

    # ===== sharpe (簡化) =====
    if len(pnls) > 1:
        sharpe = mean(pnls) / (math.sqrt(sum((p - mean(pnls))**2 for p in pnls) / len(pnls)) + 1e-9)
    else:
        sharpe = 0

    return {
        "trades": len(pnls),
        "win_rate": round(win_rate, 4),
        "total_return": round(total_return, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "equity_curve": equity[-20:],  # 最近20筆
    }
