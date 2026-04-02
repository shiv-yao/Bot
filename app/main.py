from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
import time
import traceback

app = FastAPI()


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


def _source_stats(trade_history: list[dict]) -> dict:
    buckets = {}

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        source = meta.get("source", "unknown")
        pnl = float(t.get("pnl", 0.0) or 0.0)

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
        count = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / count
        row["win_rate"] = row["wins"] / count

    return buckets


def _score_component_stats(trade_history: list[dict]) -> dict:
    rows = {
        "breakout": {"count": 0, "avg_score": 0.0},
        "smart_money": {"count": 0, "avg_score": 0.0},
        "liquidity": {"count": 0, "avg_score": 0.0},
        "momentum": {"count": 0, "avg_score": 0.0},
        "insider": {"count": 0, "avg_score": 0.0},
    }

    sums = {
        "breakout": 0.0,
        "smart_money": 0.0,
        "liquidity": 0.0,
        "momentum": 0.0,
        "insider": 0.0,
    }

    for t in trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        for key in rows.keys():
            if key in meta and meta[key] is not None:
                rows[key]["count"] += 1
                sums[key] += float(meta[key])

    for key in rows.keys():
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
        return {
            "error": f"wallet_metrics_failed: {e}"
        }


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
        return {
            "error": f"insider_metrics_failed: {e}"
        }


def _insider_vs_non_insider_performance(trade_history: list[dict], threshold: float = 0.30):
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
        pnl = float(t.get("pnl", 0.0) or 0.0)
        insider = float(meta.get("insider", 0.0) or 0.0)

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

    diff = {
        "count_diff": buckets["high_insider"]["count"] - buckets["low_insider"]["count"],
        "avg_pnl_diff": buckets["high_insider"]["avg_pnl"] - buckets["low_insider"]["avg_pnl"],
        "win_rate_diff": buckets["high_insider"]["win_rate"] - buckets["low_insider"]["win_rate"],
        "threshold": threshold,
    }

    return {
        "high_insider": buckets["high_insider"],
        "low_insider": buckets["low_insider"],
        "comparison": diff,
    }


@app.get("/debug")
def debug():
    try:
        from app.core.state import engine
        from app.core.risk_runtime import risk_engine
        from app.core import engine as engine_module
        from app.portfolio.portfolio_manager import portfolio

        now = time.time()

        cooldown_view = {
            mint[:8]: round(max(0.0, ts - now + 30), 2)
            for mint, ts in list(getattr(engine_module, "cooldown", {}).items())[-20:]
        }

        candidates_view = {
            mint[:8]: {
                "age": round(now - meta.get("time", now), 2),
                "price": meta.get("price"),
                "score": meta.get("score"),
                "source": meta.get("source"),
                "breakout": meta.get("breakout"),
                "smart_money": meta.get("smart_money"),
                "liquidity": meta.get("liquidity"),
                "insider": meta.get("insider"),
            }
            for mint, meta in list(getattr(engine_module, "candidates", {}).items())[-20:]
        }

        source_exposure = {}
        for src in ["breakout", "smart_money", "liquidity", "insider", "fusion", "unknown"]:
            try:
                ratio = portfolio.source_exposure_ratio(engine, src)
                if ratio > 0:
                    source_exposure[src] = round(ratio, 4)
            except Exception:
                continue

        positions_view = []
        for p in getattr(engine, "positions", []):
            meta = p.get("meta", {}) if isinstance(p, dict) else {}
            positions_view.append({
                "mint": p.get("mint"),
                "entry": p.get("entry"),
                "peak": p.get("peak"),
                "size": p.get("size"),
                "score": p.get("score"),
                "source": meta.get("source"),
                "breakout": meta.get("breakout"),
                "smart_money": meta.get("smart_money"),
                "liquidity": meta.get("liquidity"),
                "momentum": meta.get("momentum"),
                "insider": meta.get("insider"),
                "held_sec": round(now - p.get("time", now), 2),
            })

        return {
            "running": getattr(engine, "running", False),
            "capital": getattr(engine, "capital", 0.0),
            "peak_capital": getattr(engine, "peak_capital", 0.0),
            "regime": getattr(engine, "regime", "unknown"),
            "positions": positions_view,
            "stats": getattr(engine, "stats", {}),
            "trade_history_count": len(getattr(engine, "trade_history", [])),
            "trade_history_tail": getattr(engine, "trade_history", [])[-20:],
            "risk": {
                "equity_peak": getattr(risk_engine, "equity_peak", 0.0),
                "drawdown": risk_engine.drawdown(engine.capital),
                "daily_realized_pnl": getattr(risk_engine, "daily_realized_pnl", 0.0),
                "daily_trades": getattr(risk_engine, "daily_trades", 0),
                "cooldown_until": getattr(risk_engine, "cooldown_until", 0),
                "manual_kill": getattr(risk_engine, "manual_kill", False),
                "session_day": getattr(risk_engine, "session_day", ""),
            },
            "portfolio": {
                "total_exposure_ratio": round(portfolio.total_exposure_ratio(engine), 4),
                "can_add_more": portfolio.can_add_more(engine, max_exposure=0.75),
                "source_exposure_ratio": source_exposure,
            },
            "router_state": {
                "cooldown_count": len(getattr(engine_module, "cooldown", {})),
                "candidate_count": len(getattr(engine_module, "candidates", {})),
                "cooldown_tail": cooldown_view,
                "candidate_tail": candidates_view,
            },
            "smart_wallet": _wallet_metrics(),
            "insider": _insider_metrics(),
            "insider_vs_non_insider": _insider_vs_non_insider_performance(getattr(engine, "trade_history", [])),
            "logs": getattr(engine, "logs", [])[-120:],
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


@app.get("/metrics")
def metrics():
    try:
        from app.core.state import engine

        trade_history = getattr(engine, "trade_history", []) or []
        safe_history = [t for t in trade_history if isinstance(t, dict)]

        stats = getattr(engine, "stats", {}) or {}
        positions = getattr(engine, "positions", []) or []

        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)
        total_closed = wins + losses
        overall_win_rate = (wins / total_closed) if total_closed else 0.0

        total_realized_pnl = 0.0
        for t in safe_history:
            try:
                total_realized_pnl += float(t.get("pnl", 0.0) or 0.0)
            except Exception:
                continue

        total_trades = len(safe_history)
        avg_trade_pnl = total_realized_pnl / total_trades if total_trades else 0.0

        # ===== safe source stats =====
        source_stats = _source_stats(safe_history)
        best_source, worst_source = _best_worst_source(source_stats)
        score_component_stats = _score_component_stats(safe_history)
        insider_perf = _insider_vs_non_insider_performance(safe_history)

        positions_by_source = {}
        safe_positions = []
        for p in positions:
            if not isinstance(p, dict):
                continue
            meta = p.get("meta", {}) or {}
            src = meta.get("source", "unknown")
            positions_by_source[src] = positions_by_source.get(src, 0) + 1

            safe_positions.append({
                "mint": p.get("mint"),
                "entry": p.get("entry"),
                "peak": p.get("peak"),
                "size": p.get("size"),
                "score": p.get("score"),
                "time": p.get("time"),
                "meta": meta,
            })

        # ===== optional modules =====
        risk_block = {
            "equity_peak": None,
            "drawdown": None,
            "daily_realized_pnl": None,
            "daily_trades": None,
            "cooldown_until": None,
            "manual_kill": None,
            "session_day": None,
        }

        try:
            from app.core.risk_runtime import risk_engine
            risk_block = {
                "equity_peak": getattr(risk_engine, "equity_peak", None),
                "drawdown": risk_engine.drawdown(getattr(engine, "capital", 0.0)),
                "daily_realized_pnl": getattr(risk_engine, "daily_realized_pnl", None),
                "daily_trades": getattr(risk_engine, "daily_trades", None),
                "cooldown_until": getattr(risk_engine, "cooldown_until", None),
                "manual_kill": getattr(risk_engine, "manual_kill", None),
                "session_day": getattr(risk_engine, "session_day", None),
            }
        except Exception as e:
            risk_block["error"] = f"risk_runtime_unavailable: {e}"

        portfolio_block = {
            "total_exposure_ratio": None,
            "positions_by_source": positions_by_source,
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

        dynamic_weights = {}
        try:
            from app.alpha.combiner import get_dynamic_weights
            dynamic_weights = get_dynamic_weights(source_stats, insider_perf)
        except Exception as e:
            dynamic_weights = {"error": f"dynamic_weights_unavailable: {e}"}

        return {
            "summary": {
                "capital": getattr(engine, "capital", 0.0),
                "peak_capital": getattr(engine, "peak_capital", 0.0),
                "running": getattr(engine, "running", False),
                "regime": getattr(engine, "regime", "unknown"),
            },
            "trading": {
                "signals": int(stats.get("signals", 0) or 0),
                "executed": int(stats.get("executed", 0) or 0),
                "rejected": int(stats.get("rejected", 0) or 0),
                "errors": int(stats.get("errors", 0) or 0),
                "open_positions": len(safe_positions),
                "closed_trades": total_trades,
            },
            "performance": {
                "wins": wins,
                "losses": losses,
                "win_rate": overall_win_rate,
                "total_realized_pnl": total_realized_pnl,
                "avg_trade_pnl": avg_trade_pnl,
            },
            "source_stats": source_stats,
            "best_source": best_source,
            "worst_source": worst_source,
            "score_component_stats": score_component_stats,
            "insider_vs_non_insider_performance": insider_perf,
            "dynamic_weights": dynamic_weights,
            "portfolio": portfolio_block,
            "risk": risk_block,
            "positions": safe_positions,
            "smart_wallet": _wallet_metrics(),
            "insider": _insider_metrics(),
            "recent_trades": safe_history[-20:],
            "logs": getattr(engine, "logs", [])[-100:],
        }

    except Exception as e:
        tb = traceback.format_exc()
        try:
            from app.core.state import engine
            logs = getattr(engine, "logs", None)
            if isinstance(logs, list):
                logs.append(f"METRICS_ERROR: {str(e)}")
                logs.append(tb)
        except Exception:
            pass

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": tb[-3000:],
            },
        )


@app.get("/health")
def health():
    try:
        from app.core.state import engine

        out = {
            "ok": getattr(engine, "running", False),
            "capital": getattr(engine, "capital", 0.0),
            "positions": len(getattr(engine, "positions", []) or []),
            "errors": getattr(engine, "stats", {}).get("errors", 0),
            "regime": getattr(engine, "regime", "unknown"),
        }

        try:
            from app.core.risk_runtime import risk_engine
            out.update({
                "drawdown": risk_engine.drawdown(engine.capital),
                "daily_realized_pnl": getattr(risk_engine, "daily_realized_pnl", 0.0),
                "daily_trades": getattr(risk_engine, "daily_trades", 0),
                "manual_kill": getattr(risk_engine, "manual_kill", False),
            })
        except Exception as e:
            out["risk_error"] = str(e)

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


@app.post("/kill")
def kill():
    try:
        from app.core.state import engine
        from app.core.risk_runtime import risk_engine

        risk_engine.set_manual_kill(True)
        if hasattr(engine, "log"):
            engine.log("🔴 MANUAL KILL")
        return {"ok": True, "manual_kill": True}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


@app.post("/resume")
def resume():
    try:
        from app.core.state import engine
        from app.core.risk_runtime import risk_engine

        risk_engine.set_manual_kill(False)
        if hasattr(engine, "log"):
            engine.log("🟢 MANUAL RESUME")
        return {"ok": True, "manual_kill": False}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )
