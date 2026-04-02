import math
from statistics import mean


def _safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_div(a, b, default=0.0):
    try:
        return a / b if b else default
    except Exception:
        return default


def compute_metrics(engine):
    trades_raw = getattr(engine, "trade_history", []) or []
    trades = [t for t in trades_raw if isinstance(t, dict)]

    capital = _safe_float(getattr(engine, "capital", 0.0), 0.0)
    start_capital = _safe_float(getattr(engine, "start_capital", capital), capital)
    peak_capital = _safe_float(getattr(engine, "peak_capital", capital), capital)

    pnls = [_safe_float(t.get("pnl", 0.0), 0.0) for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(pnls)
    total_return = sum(pnls)
    win_rate = _safe_div(len(wins), total_trades, 0.0)

    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0

    if losses and sum(losses) != 0:
        profit_factor = abs(sum(wins) / sum(losses))
    else:
        profit_factor = 999.0 if wins else 0.0

    equity_curve = [1.0]
    for p in pnls:
        equity_curve.append(equity_curve[-1] * (1.0 + p))

    peak_eq = equity_curve[0] if equity_curve else 1.0
    max_dd = 0.0
    for x in equity_curve:
        if x > peak_eq:
            peak_eq = x
        dd = _safe_div((x - peak_eq), peak_eq, 0.0)
        if dd < max_dd:
            max_dd = dd

    if len(pnls) > 1:
        avg = mean(pnls)
        var = mean([(p - avg) ** 2 for p in pnls])
        std = math.sqrt(var)
        sharpe = _safe_div(avg, std + 1e-9, 0.0)
    else:
        sharpe = 0.0

    positions_raw = getattr(engine, "positions", []) or []
    positions = []
    total_exposure = 0.0

    for p in positions_raw:
        if not isinstance(p, dict):
            continue
        size = _safe_float(p.get("size", 0.0), 0.0)
        total_exposure += size
        positions.append({
            "mint": p.get("mint", ""),
            "entry": _safe_float(p.get("entry", p.get("entry_out", 0.0)), 0.0),
            "size": size,
            "score": _safe_float(p.get("score", 0.0), 0.0),
            "added": bool(p.get("added", False)),
            "tp_done": bool(p.get("tp_done", False)),
            "meta": p.get("meta", {}) if isinstance(p.get("meta", {}), dict) else {},
        })

    logs = getattr(engine, "logs", []) or []
    logs = [str(x) for x in logs[-50:]]

    stats = getattr(engine, "stats", {}) or {}

    summary = {
        "capital": round(capital, 4),
        "start_capital": round(start_capital, 4),
        "peak_capital": round(peak_capital, 4),
        "equity_gain": round(capital - start_capital, 4),
        "return_pct": round(_safe_div(capital - start_capital, start_capital, 0.0), 4) if start_capital else 0.0,
        "drawdown": round(_safe_div(capital - peak_capital, peak_capital, 0.0), 4) if peak_capital else 0.0,
        "running": bool(getattr(engine, "running", False)),
    }

    performance = {
        "trades": total_trades,
        "wins": int(stats.get("wins", len(wins)) or 0),
        "losses": int(stats.get("losses", len(losses)) or 0),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
    }

    recent_trades = []
    for t in trades[-20:]:
        recent_trades.append({
            "mint": t.get("mint", ""),
            "pnl": round(_safe_float(t.get("pnl", 0.0), 0.0), 4),
            "reason": t.get("reason", ""),
            "size": round(_safe_float(t.get("size", 0.0), 0.0), 4),
            "timestamp": _safe_float(t.get("timestamp", 0.0), 0.0),
            "meta": t.get("meta", {}) if isinstance(t.get("meta", {}), dict) else {},
        })

    return {
        "summary": summary,
        "performance": performance,
        "streak": {
            "win_streak": int(getattr(engine, "win_streak", 0) or 0),
            "loss_streak": int(getattr(engine, "loss_streak", 0) or 0),
        },
        "trading": {
            "signals": int(stats.get("signals", 0) or 0),
            "executed": int(stats.get("executed", 0) or 0),
            "rejected": int(stats.get("rejected", 0) or 0),
            "errors": int(stats.get("errors", 0) or 0),
            "open_positions": len(positions),
            "open_exposure": round(total_exposure, 4),
        },
        "positions": positions,
        "equity_curve": [round(x, 4) for x in equity_curve[-50:]],
        "recent_trades": recent_trades,
        "logs": logs,
    }
