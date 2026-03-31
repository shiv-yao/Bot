import asyncio
from app.data.market import get_quote
from app.graph.wallet_graph import wallet_graph_score
from app.edge.insider import insider_score
from app.sniper.lp import new_pool
from config.settings import SETTINGS

SOL = "So11111111111111111111111111111111111111112"


async def alpha(token):
    try:
        # 用固定 SOL 金額去買 token，比較兩次輸出
        # 這樣不會遇到 token decimals 不明的問題
        q1 = await get_quote(SOL, token, 1_000_000)   # 0.001 SOL
        if not q1:
            print(f"ALPHA_Q1_FAIL {token[:8]}")
            return 0

        await asyncio.sleep(0.3)

        q2 = await get_quote(SOL, token, 1_000_000)   # 同樣 0.001 SOL
        if not q2:
            print(f"ALPHA_Q2_FAIL {token[:8]}")
            return 0

        out1 = float(q1.get("outAmount", 0) or 0)
        out2 = float(q2.get("outAmount", 0) or 0)

        if out1 <= 0 or out2 <= 0:
            print(f"ALPHA_ZERO_OUT {token[:8]}")
            return 0

        # 關鍵：
        # SOL 固定，若第二次買到的 token 變少，表示 token 漲了
        momentum = (out1 - out2) / out1

        liq = max(0, 0.02 - float(q1.get("priceImpactPct", 1) or 1))
        flow = await wallet_graph_score(token)
        insider = await insider_score(flow)
        bonus = 0.02 if new_pool(token) else 0.0

        if momentum < SETTINGS["MOMENTUM_MIN"]:
            print(f"ALPHA_REJECT_MOM {token[:8]} momentum={momentum:.4f}")
            return 0

        if insider == 0:
            print(f"ALPHA_REJECT_INSIDER {token[:8]} flow={flow:.4f}")
            return 0

        score = momentum + liq + flow * 0.05 + insider * 0.03 + bonus

        print(
            f"ALPHA_OK {token[:8]} "
            f"momentum={momentum:.4f} "
            f"liq={liq:.4f} "
            f"flow={flow:.4f} "
            f"insider={insider:.4f} "
            f"bonus={bonus:.4f} "
            f"score={score:.4f}"
        )

        return score

    except Exception as e:
        print("ALPHA_ERR:", e)
        return 0
