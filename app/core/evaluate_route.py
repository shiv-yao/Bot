# ================= v16 GOD FULL FUSION =================

from app.core.state import engine

from app.core.score_engine import compute_score
from app.core.weight_engine import get_weights, adjust_weights

from app.alpha.wallet_alpha_v7 import get_wallet_alpha, record_token_wallets
from app.alpha.wallet_brain import wallet_rank, update_wallet
from app.alpha.wallet_graph import cluster_score, link_wallets
from app.alpha.insider_v2 import insider_score_v2, record_early_wallets
from app.alpha.helius_smart_wallet import fetch_smart_wallets

from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.insider_engine import get_token_insider_score

from app.core.risk_guard import allow_trade
from app.core.drawdown_guard import allow_trading
from app.risk.anti_rug import anti_rug_check
from app.risk.liquidity import liquidity_check
from app.core.risk_runtime import risk_engine

from app.portfolio.portfolio_manager import portfolio
from app.portfolio.allocator import get_position_size

from app.execution.executor import execute_buy
from app.core.pricing import get_price
from app.pnl.pnl_engine import record_trade


async def evaluate_route(route):
    mint = route.get("mint")
    token = (
        route.get("token")
        or route.get("symbol")
        or route.get("name")
        or mint
    )

    if not mint:
        return

    # ================= GLOBAL RISK =================
    if not allow_trading(engine):
        return

    if not allow_trade(engine):
        return

    if hasattr(portfolio, "can_add_more") and not portfolio.can_add_more(engine):
        return

    # ================= WALLET =================
    wallets = await fetch_smart_wallets(mint)
    record_token_wallets(mint, wallets)
    link_wallets(wallets)
    record_early_wallets(mint, wallets)

    wa = get_wallet_alpha(mint)
    if not wa:
        return

    top_wallet = wa["top_wallet"]

    # ================= ALPHA =================
    try:
        alpha = {
            "b": breakout_score(token),
            "s": await smart_money_score(mint),
            "l": liquidity_score(token),
            "i": get_token_insider_score(mint),
            "w": wa["avg"],
            "c": cluster_score(top_wallet),
            "i2": insider_score_v2(mint),
        }
    except Exception as e:
        engine.log(f"ALPHA_ERR {mint[:6]} {e}")
        return

    weights = get_weights()
    score = compute_score(alpha, weights)
    rank = wallet_rank(top_wallet)

    # ================= FILTER =================
    if score < 0.25:
        return

    if not await anti_rug_check(mint):
        return

    if not await liquidity_check(mint):
        return

    # ================= SIZE =================
    try:
        size = get_position_size(engine.capital, score)
    except TypeError:
        # 相容某些 allocator 簽名不同
        size = get_position_size(score, engine.capital, engine)

    if rank > 0.7:
        size *= 2

    if rank > 0.85:
        size *= 3

    # ================= EXECUTION =================
    ok = await execute_buy(mint, size)
    if not ok:
        return

    price = await get_price(token)
    if not price:
        return

    pos = {
        "mint": mint,
        "entry": price,
        "size": size,
        "wallet": top_wallet,
        "score": score,
        "peak": price,
    }

    engine.positions.append(pos)

    engine.log(
        f"BUY {mint[:6]} "
        f"token={token} "
        f"score={score:.3f} "
        f"rank={rank:.2f} "
        f"size={size:.4f}"
    )


async def handle_exit(pos, price):
    pnl = (price - pos["entry"]) / pos["entry"]

    update_wallet(pos.get("wallet"), pnl)
    adjust_weights(pnl)

    if hasattr(risk_engine, "record_realized"):
        risk_engine.record_realized(pnl)

    record_trade(engine, pos, pnl)

    engine.capital += pos["size"] * (1 + pnl)

    engine.log(f"SELL {pos['mint'][:6]} pnl={pnl:.3f}")
