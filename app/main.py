from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
import time
import traceback

app = FastAPI()


# ================= STARTUP =================
@app.on_event("startup")
async def startup():
    print("🚀 SYSTEM START")
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("✅ ENGINE STARTED")
    except Exception as e:
        print("❌ STARTUP IMPORT ERROR:", repr(e))


@app.get("/")
def root():
    return {"status": "RUNNING"}


# ================= SAFE HELPERS =================
def safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default


# ================= SOURCE STATS =================
def _source_stats(trade_history):
    buckets = {}

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        source = meta.get("source", "unknown")
        pnl = safe_float(t.get("pnl"))

        if source not in buckets:
            buckets[source] = {
                "count": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
            }

        buckets[source]["count"] += 1
        buckets[source]["total_pnl"] += pnl

        if pnl >= 0:
            buckets[source]["wins"] += 1
        else:
            buckets[source]["losses"] += 1

    for src, row in buckets.items():
        c = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / c
        row["win_rate"] = row["wins"] / c

    return buckets


def _best_worst_source(source_stats):
    if not source_stats:
        return None, None

    items = list(source_stats.items())
    best = max(items, key=lambda kv: kv[1]["avg_pnl"])
    worst = min(items, key=lambda kv: kv[1]["avg_pnl"])

    return (
        {"source": best[0], **best[1]},
        {"source": worst[0], **worst[1]},
    )


# ================= SCORE COMPONENT =================
def _score_component_stats(trade_history):
    keys = ["breakout", "smart_money", "liquidity", "momentum", "insider"]

    rows = {k: {"count": 0, "avg_score": 0.0} for k in keys}
    sums = {k: 0.0 for k in keys}

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        for k in keys:
            if k in meta:
                rows[k]["count"] += 1
                sums[k] += safe_float(meta.get(k))

    for k in keys:
        c = rows[k]["count"]
        rows[k]["avg_score"] = sums[k] / c if c else 0.0

    return rows


# ================= INSIDER PERFORMANCE =================
def _insider_vs_non_insider_performance(trade_history, threshold=0.10):
    buckets = {
        "high_insider": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0},
        "low_insider": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0},
    }

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        pnl = safe_float(t.get("pnl"))
        insider = safe_float(meta.get("insider"))

        name = "high_insider" if insider >= threshold else "low_insider"
        row = buckets[name]

        row["count"] += 1
        row["total_pnl"] += pnl

        if pnl >= 0:
            row["wins"] += 1
        else:
            row["losses"] += 1

    for row in buckets.values():
        c = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / c
        row["win_rate"] = row["wins"] / c

    buckets["comparison"] = {
        "avg_pnl_diff": buckets["high_insider"]["avg_pnl"]
        - buckets["low_insider"]["avg_pnl"],
        "win_rate_diff": buckets["high_insider"]["win_rate"]
        - buckets["low_insider"]["win_rate"],
        "threshold": threshold,
    }

    return buckets


# ================= METRICS =================
@app.get("/metrics")
def metrics():
    try:
        from app.state import engine

        trade_history = getattr(engine, "trade_history", []) or []
        safe_history = [t for t in trade_history if isinstance(t, dict)]

        stats = getattr(engine, "stats", {}) or {}
        positions = getattr(engine, "positions", []) or []

        wins = int(stats.get("wins", 0))
        losses = int(stats.get("losses", 0))

        total_closed = wins + losses
        win_rate = wins / total_closed if total_closed else 0.0

        total_pnl = sum(safe_float(t.get("pnl")) for t in safe_history)
        total_trades = len(safe_history)

        avg_pnl = total_pnl / total_trades if total_trades else 0.0

        # ===== TRADE TYPE =====
        partial_trades = [t for t in safe_history if t.get("reason") == "PARTIAL"]
        full_trades = [t for t in safe_history if t.get("reason") != "PARTIAL"]

        # ===== SOURCE =====
        source_stats = _source_stats(safe_history)
        best_source, worst_source = _best_worst_source(source_stats)

        # ===== COMPONENT =====
        score_stats = _score_component_stats(safe_history)

        # ===== INSIDER =====
        insider_perf = _insider_vs_non_insider_performance(safe_history)

        # ===== POSITIONS =====
        positions_by_source = {}
        for p in positions:
            meta = p.get("meta", {}) or {}
            src = meta.get("source", "unknown")
            positions_by_source[src] = positions_by_source.get(src, 0) + 1

        return {
            "summary": {
                "capital": getattr(engine, "capital", 0),
                "peak_capital": getattr(engine, "peak_capital", 0),
                "running": getattr(engine, "running", False),
            },
            "trading": {
                "signals": stats.get("signals", 0),
                "executed": stats.get("executed", 0),
                "errors": stats.get("errors", 0),
                "open_positions": len(positions),
                "closed_trades": len(full_trades),
                "partial_trades": len(partial_trades),
                "total_events": total_trades,
            },
            "performance": {
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_trade_pnl": avg_pnl,
            },
            "source_stats": source_stats,
            "best_source": best_source,
            "worst_source": worst_source,
            "score_component": score_stats,
            "insider": insider_perf,
            "positions_by_source": positions_by_source,
            "recent_trades": safe_history[-20:],
            "logs": getattr(engine, "logs", [])[-100:],
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc()[-2000:],
            },
        )


# ================= HEALTH =================
@app.get("/health")
def health():
    try:
        from app.state import engine

        return {
            "ok": engine.running,
            "capital": engine.capital,
            "positions": len(engine.positions),
            "errors": engine.stats.get("errors", 0),
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
