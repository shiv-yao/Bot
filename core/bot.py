
import asyncio
from core.alpha import compute_alpha
from core.rl_agent import RLAgent
from core.gnn_engine import WalletGraph
from core.automl_engine import StrategyBrain
from core.execution_optimizer import ExecutionOptimizer
from core.scanner import scan_tokens

rl = RLAgent()
gnn = WalletGraph()
brain = StrategyBrain()
exec_opt = ExecutionOptimizer()

async def run():
    print("🔥 V∞ FINAL SYSTEM")

    while True:
        tokens = await scan_tokens()

        for t in tokens:
            strength, liq, impact = 0.01, 80000, 0.1

            alpha = compute_alpha(strength, liq, impact)

            state = (strength, liq/100000, impact)
            action = rl.choose(state)

            if action == "skip":
                continue
            elif action == "small":
                size = 0.005
            elif action == "medium":
                size = 0.01
            else:
                size = 0.02

            method = exec_opt.choose(alpha, impact)

            print(f"TRADE {t} alpha={alpha:.2f} size={size} via {method}")

            pnl = 0.02

            rl.update(state, action, pnl)
            gnn.update_trade("wallet1", t, pnl)
            brain.update(pnl)

        await asyncio.sleep(2)
