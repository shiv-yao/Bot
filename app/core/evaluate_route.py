# ================= v16 GOD FULL FUSION =================

from app.core.state import engine

# ===== scoring =====
from app.core.score_engine import compute_score
from app.core.weight_engine import get_weights, adjust_weights

# ===== wallet（全部保留）=====
from app.alpha.wallet_alpha_v7 import get_wallet_alpha, record_token_wallets
from app.alpha.wallet_brain import wallet_rank, update_wallet
from app.alpha.wallet_graph import cluster_score
from app.alpha.helius_wallet_tracker import update_token_wallets
from app.alpha.helius_smart_wallet import fetch_smart_wallets

# ===== alpha（全部保留）=====
from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.insider_engine import get_token_insider_score
from app.alpha.insider_v2 import insider_score_v2

# ===== risk（全部保留）=====
from app.core.risk_guard import allow_trade
from app.core.drawdown_guard import allow_trading
from app.risk.anti_rug import anti_rug_check
from app.risk.liquidity import liquidity_check
from app.core.risk_runtime import risk_engine

# ===== portfolio（全部保留）=====
from app.portfolio.portfolio_manager import portfolio
from app.portfolio.allocator import get_position_size

# ===== execution（全部保留）=====
from app.execution.executor import execute_buy
from app.execution.jupiter import get_quote
from app.execution.jito import send_bundle

# ===== mempool =====
from app.mempool.decode import decode_tx

# ===== utils =====
from app.core.pricing import get_price
from app.pnl.pnl_engine import record_trade


async def evaluate_route(route):

    mint = route.get("mint")
    token = route.get("token")

    if not mint or not token:
        return

    # ================= GLOBAL RISK =================
    if not allow_trading(engine):
        return

    if not allow_trade(engine):
        return

    if not portfolio.can_add_more(engine):
        return

    # ================= WALLET（全保留）=================
    wallets = await fetch_smart_wallets(mint)
    await update_token_wallets(mint)

    record_token_wallets(mint, wallets)

    wa = get_wallet_alpha(mint)
    if not wa:
        return

    top_wallet = wa["top_wallet"]

    # ================= ALPHA（全保留）=================
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
    except Exception:
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

    # ================= SIZE（portfolio 控制）=================
    size = get_position_size(engine.capital, score)

    if rank > 0.7:
        size *= 2

    if rank > 0.85:
        size *= 3

    # ================= EXECUTION =================
    ok = await execute_buy(mint, size)

    if not ok:
        return

    # ================= PRICE =================
    price = await get_price(token)
    if not price:
        return

    pos = {
        "mint": mint,
        "entry": price,
        "size": size,
        "wallet": top_wallet,
        "score": score,
    }

    engine.positions.append(pos)

    engine.log(
        f"BUY {mint[:6]} "
        f"score={score:.3f} "
        f"rank={rank:.2f} "
        f"size={size:.4f}"
    )


# ================= EXIT / LEARNING =================

async def handle_exit(pos, price):

    pnl = (price - pos["entry"]) / pos["entry"]

    # learning
    update_wallet(pos["wallet"], pnl)
    adjust_weights(pnl)
    record_trade(engine, pos, pnl)

    # risk engine
    risk_engine.record_realized(pnl)

    engine.capital += pos["size"] * (1 + pnl)

    engine.log(f"SELL {pos['mint']} pnl={pnl:.3f}")
