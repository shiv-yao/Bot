from app.alpha.wallet_tracker import get_wallets_for_token
from app.alpha.smart_wallet_ranker import rank_wallets


def smart_money_score(token: dict) -> float:
    mint = token.get("mint")

    wallets = get_wallets_for_token(mint)

    wallet_strength = rank_wallets(wallets)

    return wallet_strength
