import math
from statistics import mean


def safe_div(a, b):
    return a / b if b != 0 else 0


def compute_metrics(engine):
    trades = engine.trade_history

    # ===== 基本 =====
    capital = engine.capital
    start = getattr(engine, "start_capital", capital)
    peak = engine.peak_capital

    # ===== pnl =====
    pnls = [float(t.get("pnl", 0)) for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_return = sum(pnls)
    total_trades = len(pnls)

    win_rate = safe_div(len(wins), total_trades)

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

    # ===== drawdown =====
    peak_eq = equity[0] if equity else 1
    max_dd = 0

    for x in equity:
        if x > peak_eq:
            peak_eq = x
        dd = (x - peak_eq) / peak_eq
        if dd < max_dd:
            max_dd = dd

    # ===== sharpe =====
    if len(pnls) > 1:
        avg = mean(pnls)
        std = math.sqrt(mean([(p - avg) ** 2 for p in pnls]))
        sharpe = avg / (std + 1e-9)
    else:
        sharpe = 0

    # ===== positions =====
    positions = []
    total_exposure = 0

    for p in engine.positions:
        total_exposure += p.get("size", 0)

        positions.append({
            "mint": p.get("mint"),
            "size": round(p.get("size", 0), 4),
            "entry": p.get("entry", 0),
            "added": p.get("added", False),
            "tp_done": p.get("tp_done", False),
        })

    # ===== streak =====
    win_streak = getattr(engine, "win_streak", 0)
    loss_streak = getattr(engine, "loss_streak", 0)

    # ===== summary =====
    summary = {
        "capital": round(capital, 4),
        "start_capital": round(start, 4),
        "peak_capital": round(peak, 4),
        "return_pct": round(capital / start - 1, 4) if start else 0,
        "drawdown": round((capital - peak) / peak, 4) if peak else 0,
    }

    # ===== performance =====
    performance = {
        "trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
    }

    # ===== recent trades =====
    recent_trades = trades[-10:]

    return {
        "summary": summary,
        "performance": performance,
        "streak": {
            "win_streak": win_streak,
            "loss_streak": loss_streak,
        },
        "positions": positions,
        "exposure": round(total_exposure, 4),
        "equity_curve": equity[-50:],  # 最近50點
        "recent_trades": recent_trades,
        "logs": engine.logs[-50:],
    }
