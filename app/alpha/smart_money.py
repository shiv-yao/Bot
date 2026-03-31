from app.alpha.helius_wallet_tracker import get_wallets_for_token
from app.alpha.smart_wallet_ranker import rank_wallets
from app.alpha.wallet_graph import get_token_graph_score


def smart_money_score(token: dict) -> float:
    """
    真 smart money 分數：
    1. wallet ranking
    2. wallet graph 關聯分數
    """
    mint = token.get("mint")
    if not mint:
        return 0.0

    wallets = get_wallets_for_token(mint)

    wallet_score = rank_wallets(wallets)
    graph_score = get_token_graph_score(mint)

    final = wallet_score * 0.6 + graph_score * 0.4

    return min(max(final, 0.0), 1.0)
