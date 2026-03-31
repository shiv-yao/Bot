from app.data.market import get_quote
from app.graph.wallet_graph import wallet_graph_score
from app.edge.insider import insider_score
from app.sniper.lp import new_pool
from config.settings import SETTINGS

SOL = "So11111111111111111111111111111111111111112"


async def alpha(token):
    try:
        # 🔥 一次就夠（不要再 sleep）
        q = await get_quote(SOL, token, 1_000_000)
        if not q:
            return 0

        out = float(q.get("outAmount", 0) or 0)
        impact = float(q.get("priceImpactPct", 0) or 0)

        if out <= 0:
            return 0

        # 🔥 用 liquidity + impact 當動能 proxy
        momentum = max(0, 0.02 - impact)

        flow = await wallet_graph_score(token)
        insider = await insider_score(flow)
        bonus = 0.02 if new_pool(token) else 0.0

        # ❗ 關鍵：放寬條件
        if momentum < SETTINGS["MOMENTUM_MIN"]:
            print(f"ALPHA_REJECT_MOM {token[:8]} mom={momentum:.4f}")
            return 0

        score = momentum + flow * 0.05 + insider * 0.03 + bonus

        print(
            f"ALPHA_OK {token[:8]} "
            f"mom={momentum:.4f} "
            f"flow={flow:.4f} "
            f"insider={insider:.4f} "
            f"score={score:.4f}"
        )

        return score

    except Exception as e:
        print("ALPHA_ERR:", e)
        return 0
