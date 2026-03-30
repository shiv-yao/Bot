import asyncio
from app.data.market import get_quote
from app.graph.wallet_graph import wallet_graph_score
from app.edge.insider import insider_score
from app.sniper.lp import new_pool
from config.settings import SETTINGS
SOL = "So11111111111111111111111111111111111111112"
async def alpha(token):
    q1 = await get_quote(token, SOL, 1_000_000)
    if not q1: return 0
    await asyncio.sleep(0.2)
    q2 = await get_quote(token, SOL, 1_000_000)
    if not q2: return 0
    p1 = float(q1.get("outAmount", 0)); p2 = float(q2.get("outAmount", 0))
    if p1 <= 0: return 0
    momentum = (p2 - p1) / p1
    liq = max(0, 0.02 - float(q1.get("priceImpactPct", 1)))
    flow = await wallet_graph_score(token)
    insider = await insider_score(flow)
    bonus = 0.02 if new_pool(token) else 0
    if momentum < SETTINGS["MOMENTUM_MIN"] or insider == 0: return 0
    return momentum + liq + flow * 0.05 + insider * 0.03 + bonus
