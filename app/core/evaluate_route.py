from app.alpha.wallet_alpha import get_token_wallet_alpha, get_wallet_score
from app.alpha.helius_wallet_tracker import update_token_wallets


async def evaluate_route(mint, base_score):
    wallets = await update_token_wallets(mint)

    wallet_alpha_avg, wallet_best, wallet_cluster, wallet_copy = get_token_wallet_alpha(mint)

    # early wallet bonus
    early_bonus = 0
    for i, w in enumerate(wallets[:3]):
        early_bonus += get_wallet_score(w) * (1 - i * 0.3)

    wallet_score = (
        wallet_alpha_avg * 0.4 +
        wallet_best * 0.3 +
        wallet_cluster * 0.15 +
        wallet_copy * 0.15 +
        early_bonus
    )

    wallet_score = min(wallet_score, 0.5)

    final_score = base_score + wallet_score

    # 🚨 沒 edge 不做
    if wallet_alpha_avg < 0.04 and wallet_best < 0.1:
        print("REJECT_NO_EDGE", mint)
        return None

    return final_score, {
        "wallet_alpha_avg": wallet_alpha_avg,
        "wallet_best": wallet_best,
        "wallet_cluster": wallet_cluster,
        "wallet_copy_signal": wallet_copy,
        "wallets": wallets,
    }
