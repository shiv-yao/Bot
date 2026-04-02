import asyncio

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.allocator import get_position_size
from app.core.position_manager import check_exit

from app.core.score_engine import compute_score
from app.core.weight_engine import get_weights, adjust_weights

from app.alpha.wallet_alpha_v7 import get_wallet_alpha, record_token_wallets
from app.alpha.wallet_brain import update_wallet, wallet_rank
from app.alpha.helius_smart_wallet import fetch_smart_wallets

from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.insider_engine import get_token_insider_score
from app.alpha.wallet_graph import cluster_score
from app.alpha.insider_v2 import insider_score_v2


async def evaluate_route(route):
    mint = route["mint"]
    token = route["token"]

    wallets = await fetch_smart_wallets(mint)
    record_token_wallets(mint, wallets)

    wa = get_wallet_alpha(mint)
    if not wa:
        return

    top_wallet = wa["top_wallet"]

    alpha = {
        "b": breakout_score(token),
        "s": await smart_money_score(mint),
        "l": liquidity_score(token),
        "i": get_token_insider_score(mint),
        "w": wa["avg"],
        "c": cluster_score(top_wallet),
        "i2": insider_score_v2(mint),
    }

    score = compute_score(alpha, get_weights())
    rank = wallet_rank(top_wallet)

    size = get_position_size(engine.capital, score)

    if rank > 0.7:
        size *= 2
    if rank > 0.85:
        size *= 3

    price = await get_price(token)
    if not price:
        return

    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": size,
        "wallet": top_wallet
    })

    engine.log(f"BUY {mint} score={score:.3f} rank={rank:.2f}")


async def manage_positions():
    remaining = []

    for pos in engine.positions:
        price = await get_price(pos["mint"])

        if price > pos["peak"]:
            pos["peak"] = price

        exit_reason = check_exit(pos, price)

        if exit_reason:
            pnl = (price - pos["entry"]) / pos["entry"]

            update_wallet(pos["wallet"], pnl)
            adjust_weights(pnl)

            engine.capital += pos["size"] * (1 + pnl)

            engine.log(f"SELL {pos['mint']} {exit_reason} pnl={pnl:.3f}")
        else:
            remaining.append(pos)

    engine.positions = remaining


async def main_loop():
    engine.log("🚀 v13 PRODUCT ENGINE START")

    while engine.running:
        try:
            await manage_positions()

            tokens = await scan()

            for t in tokens:
                await evaluate_route(t)

        except Exception as e:
            engine.log(f"ERR {e}")

        await asyncio.sleep(2)
