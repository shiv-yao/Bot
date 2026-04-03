import math
from statistics import mean


def _safe(x, d=0.0):
    try:
        return float(x)
    except:
        return d


def compute_metrics(engine):
    trades = [t for t in getattr(engine, "trade_history", []) if isinstance(t, dict)]

    capital = _safe(getattr(engine, "capital", 0.0))
    start = _safe(getattr(engine, "start_capital", capital))
    peak = _safe(getattr(engine, "peak_capital", capital))

    pnls = [_safe(t.get("pnl", 0)) for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(pnls)

    win_rate = len(wins) / total if total else 0.0

    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0

    profit_factor = (
        abs(sum(wins) / sum(losses))
        if losses and sum(losses) != 0
        else (2.0 if wins else 0.0)
    )

    # ===== equity =====
    equity = [1.0]
    for p in pnls:
        equity.append(equity[-1] * (1 + p))

    peak_eq = 1.0
    max_dd = 0.0

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
        sharpe = 0.0

    # ===== exposure =====
    positions = getattr(engine, "positions", []) or []
    exposure = sum(_safe(p.get("size", 0)) for p in positions)

    # ===== forced trades =====
    forced = sum(
        1 for t in trades
        if isinstance(t.get("meta", {}), dict)
        and t.get("meta", {}).get("forced", False)
    )

    stats = getattr(engine, "stats", {}) or {}

    return {
        "summary": {
            "capital": round(capital, 4),
            "start_capital": round(start, 4),
            "peak_capital": round(peak, 4),
            "equity_gain": round(capital - start, 4),
            "return_pct": round((capital - start) / start, 4) if start else 0.0,
            "drawdown": round((capital - peak) / peak, 4) if peak else 0.0,
            "running": getattr(engine, "running", False),
        },
        "performance": {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 4),
            "total_return": round(sum(pnls), 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe": round(sharpe, 4),
        },
        "trading": {
            "signals": stats.get("signals", 0),
            "executed": stats.get("executed", 0),
            "rejected": stats.get("rejected", 0),
            "errors": stats.get("errors", 0),
            "open_positions": len(positions),
            "open_exposure": round(exposure, 4),
            "forced_trades": forced,
            "no_trade_cycles": getattr(engine, "no_trade_cycles", 0),
        },
        "equity_curve": equity[-50:],
        "logs": getattr(engine, "logs", [])[-50:],
    }
