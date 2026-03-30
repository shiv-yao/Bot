
import asyncio
from app.state import engine
from app.alpha.alpha_v5 import score
from app.fund.allocator import size
from app.rl.ppo import train
from app.execution.jupiter import execute_trade
from app.execution.mempool import get_tokens

async def bot_loop():
    while True:
        tokens=await get_tokens()
        for t in tokens:
            s=score(t)
            if s>0.5:
                amt=size(engine.capital)
                entry,exit=await execute_trade(t,amt)
                pnl=(exit-entry)*amt
                engine.capital+=pnl
                engine.pnl+=pnl
                engine.trades.append({"token":t,"pnl":pnl})
                train([1,1,0,1,0],pnl)
                engine.logs.append(f"TRADE {t} pnl={pnl}")
        await asyncio.sleep(2)
