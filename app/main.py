from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
import time
import traceback

app = FastAPI()


# =========================
# STARTUP
# =========================
@app.on_event("startup")
async def startup():
    print("🚀 SYSTEM START")
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("✅ ENGINE STARTED")
    except Exception as e:
        print("❌ STARTUP IMPORT ERROR:", repr(e))


# =========================
# SAFE HELPERS
# =========================
def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


# =========================
# ROOT
# =========================
@app.get("/")
def root():
    try:
        from app.state import engine
        return {
            "status": "RUNNING",
            "running": getattr(engine, "running", False),
            "capital": safe_float(getattr(engine, "capital", 0.0)),
            "positions": len(getattr(engine, "positions", []) or []),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
        }


# =========================
# INTERNAL STATS HELPERS
# =========================
def _source_stats(trade_history: list[dict]) -> dict:
    buckets = {}

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        source = meta.get("source", "unknown")
        pnl = safe_float(t.get("pnl", 0.0))

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

    for source, row in buckets.items():
        count = max(int(row["count"]), 1)
        row["avg_pnl"] = row["total_pnl"] / count
        row["win_rate"] = row["wins"] / count

    return buckets


def _score_component_stats(trade_history: list[dict]) -> dict:
    keys = ["breakout", "smart_money", "liquidity", "momentum", "insider"]

    rows = {k: {"count": 0, "avg_score": 0.0} for k in keys}
    sums = {k: 0.0 for k in keys}

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        for key in keys:
            if key in meta and meta[key] is not None:
                rows[key]["count"] += 1
                sums[key] += safe_float(meta[key])

    for key in keys:
        c = rows[key]["count"]
        rows[key]["avg_score"] = (sums[key] / c) if c else 0.0

    return rows


def _best_worst_source(source_stats: dict):
    if not source_stats:
        return None, None

    items = list(source_stats.items())
    best = max(items, key=lambda kv: kv[1]["avg_pnl"])
    worst = min(items, key=lambda kv: kv[1]["avg_pnl"])

    return (
        {"source": best[0], **best[1]},
        {"source": worst[0], **worst[1]},
    )


def _insider_vs_non_insider_performance(
    trade_history: list[dict],
    threshold: float = 0.10,
) -> dict:
    buckets = {
        "high_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
        "low_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
    }

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        pnl = safe_float(t.get("pnl", 0.0))
        insider = safe_float(meta.get("insider", 0.0))

        bucket_name = "high_insider" if insider >= threshold else "low_insider"
        bucket = buckets[bucket_name]

        bucket["count"] += 1
        bucket["total_pnl"] += pnl

        if pnl >= 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    for bucket in buckets.values():
        count = max(bucket["count"], 1)
        bucket["avg_pnl"] = bucket["total_pnl"] / count
        bucket["win_rate"] = bucket["wins"] / count

    buckets["comparison"] = {
        "count_diff": buckets["high_insider"]["count"] - buckets["low_insider"]["count"],
        "avg_pnl_diff": buckets["high_insider"]["avg_pnl"] - buckets["low_insider"]["avg_pnl"],
        "win_rate_diff": buckets["high_insider"]["win_rate"] - buckets["low_insider"]["win_rate"],
        "threshold": threshold,
    }

    return buckets


def _wallet_metrics():
    try:
        from app.alpha.helius_wallet_tracker import token_wallets
        from app.alpha.wallet_tracker import wallet_trades
        from app.alpha.smart_wallet_ranker import wallet_score

        wallet_count_by_token = {
            mint[:8]: len(wallets)
            for mint, wallets in list(token_wallets.items())[-30:]
        }

        top_wallet_strength = []
        for wallet in list(wallet_trades.keys())[-50:]:
            score = wallet_score(wallet)
            top_wallet_strength.append({
                "wallet": wallet,
                "score": round(score, 4),
                "trades": len(wallet_trades.get(wallet, [])),
            })

        top_wallet_strength.sort(key=lambda x: x["score"], reverse=True)
        top_wallet_strength = top_wallet_strength[:10]

        return {
            "tracked_wallets": len(wallet_trades),
            "tracked_tokens": len(token_wallets),
            "wallet_count_by_token": wallet_count_by_token,
            "top_wallet_strength": top_wallet_strength,
        }
    except Exception as e:
        return {"error": f"wallet_metrics_failed: {e}"}


def _insider_metrics():
    try:
        from app.alpha.insider_engine import (
            token_early_wallets,
            wallet_insider_hits,
            get_insider_summary,
        )

        token_summary = {}
        for mint in list(token_early_wallets.keys())[-20:]:
            token_summary[mint[:8]] = get_insider_summary(mint)

        top_wallets = sorted(
            [{"wallet": w, "hits": h} for w, h in wallet_insider_hits.items()],
            key=lambda x: x["hits"],
            reverse=True,
        )[:10]

        return {
            "tracked_tokens": len(token_early_wallets),
            "top_wallet_hits": top_wallets,
            "token_insider_summary": token_summary,
        }
    except Exception as e:
        return {"error": f"insider_metrics_failed: {e}"}


# =========================
# DEBUG
# =========================
@app.get("/debug")
def debug():
    try:
        from app.state import engine

        now = time.time()

        positions_view = []
        for p in getattr(engine, "positions", []) or []:
            if not isinstance(p, dict):
                continue

            meta = p.get("meta", {}) or {}
            positions_view.append({
                "mint": p.get("mint"),
                "entry": p.get("entry"),
                "size": p.get("size"),
                "score": p.get("score"),
                "source": meta.get("source"),
                "breakout": meta.get("breakout"),
                "smart_money": meta.get("smart_money"),
                "liquidity": meta.get("liquidity"),
                "momentum": meta.get("momentum"),
                "insider": meta.get("insider"),
                "held_sec": round(now - safe_float(p.get("time", now), now), 2),
                "added": p.get("added", False),
                "tp_done": p.get("tp_done", False),
            })

        return {
            "running": getattr(engine, "running", False),
            "capital": safe_float(getattr(engine, "capital", 0.0)),
            "start_capital": safe_float(getattr(engine, "start_capital", 0.0)),
            "peak_capital": safe_float(getattr(engine, "peak_capital", 0.0)),
            "regime": getattr(engine, "regime", "unknown"),
            "positions": positions_view,
            "stats": getattr(engine, "stats", {}),
            "trade_history_count": len(getattr(engine, "trade_history", []) or []),
            "trade_history_tail": (getattr(engine, "trade_history", []) or [])[-20:],
            "streak": {
                "win_streak": getattr(engine, "win_streak", 0),
                "loss_streak": getattr(engine, "loss_streak", 0),
            },
            "last_signal": getattr(engine, "last_signal", ""),
            "last_trade": getattr(engine, "last_trade", ""),
            "smart_wallet": _wallet_metrics(),
            "insider": _insider_metrics(),
            "insider_vs_non_insider": _insider_vs_non_insider_performance(
                getattr(engine, "trade_history", []) or []
            ),
            "logs": (getattr(engine, "logs", []) or [])[-120:],
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc()[-3000:],
            },
        )


# =========================
# METRICS
# =========================
@app.get("/metrics")
def metrics():
    try:
        from app.state import engine
        from app.metrics import compute_metrics

        trade_history = getattr(engine, "trade_history", []) or []
        safe_history = [t for t in trade_history if isinstance(t, dict)]

        source_stats = _source_stats(safe_history)
        best_source, worst_source = _best_worst_source(source_stats)
        score_component_stats = _score_component_stats(safe_history)
        insider_perf = _insider_vs_non_insider_performance(safe_history)

        base_metrics = compute_metrics(engine) or {}

        positions = getattr(engine, "positions", []) or []
        positions_by_source = {}
        for p in positions:
            if not isinstance(p, dict):
                continue
            src = (p.get("meta", {}) or {}).get("source", "unknown")
            positions_by_source[src] = positions_by_source.get(src, 0) + 1

        partial_trades = [t for t in safe_history if t.get("reason") == "PARTIAL"]
        full_exit_trades = [t for t in safe_history if t.get("reason") != "PARTIAL"]

        dynamic_weights = {}
        try:
            from app.alpha.combiner import get_dynamic_weights
            dynamic_weights = get_dynamic_weights(source_stats, insider_perf)
        except Exception as e:
            dynamic_weights = {"error": f"dynamic_weights_unavailable: {e}"}

        portfolio_block = {
            "positions_by_source": positions_by_source,
            "total_exposure_ratio": None,
            "source_exposure_ratio": {},
        }

        try:
            from app.portfolio.portfolio_manager import portfolio
            portfolio_block["total_exposure_ratio"] = round(
                portfolio.total_exposure_ratio(engine), 4
            )
            portfolio_block["source_exposure_ratio"] = {
                src: round(portfolio.source_exposure_ratio(engine, src), 4)
                for src in positions_by_source.keys()
            }
        except Exception as e:
            portfolio_block["error"] = f"portfolio_unavailable: {e}"

        return {
            "summary": base_metrics.get("summary", {}),
            "performance": base_metrics.get("performance", {}),
            "streak": base_metrics.get("streak", {}),
            "trading": {
                **base_metrics.get("trading", {}),
                "closed_trades": len(full_exit_trades),
                "partial_trades": len(partial_trades),
                "total_trade_events": len(safe_history),
            },
            "positions": base_metrics.get("positions", []),
            "equity_curve": base_metrics.get("equity_curve", []),
            "recent_trades": base_metrics.get("recent_trades", []),
            "logs": base_metrics.get("logs", []),
            "source_stats": source_stats,
            "best_source": best_source,
            "worst_source": worst_source,
            "score_component_stats": score_component_stats,
            "insider_vs_non_insider_performance": insider_perf,
            "dynamic_weights": dynamic_weights,
            "portfolio": portfolio_block,
            "smart_wallet": _wallet_metrics(),
            "insider": _insider_metrics(),
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc()[-3000:],
            },
        )


# =========================
# HEALTH
# =========================
@app.get("/health")
def health():
    try:
        from app.state import engine

        out = {
            "ok": getattr(engine, "running", False),
            "capital": safe_float(getattr(engine, "capital", 0.0)),
            "positions": len(getattr(engine, "positions", []) or []),
            "errors": (getattr(engine, "stats", {}) or {}).get("errors", 0),
            "regime": getattr(engine, "regime", "unknown"),
            "win_streak": getattr(engine, "win_streak", 0),
            "loss_streak": getattr(engine, "loss_streak", 0),
        }

        try:
            from app.portfolio.portfolio_manager import portfolio
            out["total_exposure_ratio"] = round(portfolio.total_exposure_ratio(engine), 4)
        except Exception as e:
            out["portfolio_error"] = str(e)

        return out

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc()[-2000:],
            },
        )


# =========================
# MANUAL CONTROL
# =========================
@app.post("/kill")
def kill():
    try:
        from app.state import engine
        engine.running = False
        if hasattr(engine, "log"):
            engine.log("🔴 MANUAL KILL")
        else:
            logs = getattr(engine, "logs", None)
            if isinstance(logs, list):
                logs.append("🔴 MANUAL KILL")
        return {"ok": True, "running": False}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


@app.post("/resume")
def resume():
    try:
        from app.state import engine
        engine.running = True
        if hasattr(engine, "log"):
            engine.log("🟢 MANUAL RESUME")
        else:
            logs = getattr(engine, "logs", None)
            if isinstance(logs, list):
                logs.append("🟢 MANUAL RESUME")
        return {"ok": True, "running": True}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )
