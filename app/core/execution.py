# app/core/execution.py

from app.core import state

def execute_buy(mint, price, size):
    if state.MODE == "PAPER":
        state.capital -= size

        state.positions.append({
            "mint": mint,
            "entry": price,
            "peak": price,
            "size": size
        })

        state.stats["executed"] += 1
        state.logs.append(f"BUY {mint[:6]} {price:.4f}")

        return True

    # 🔥 REAL（先留空）
    return False


def execute_sell(pos, price):
    pnl = (price - pos["entry"]) / pos["entry"]

    state.capital += pos["size"] * (1 + pnl)

    state.logs.append(f"SELL {pos['mint'][:6]} pnl={pnl:.4f}")
