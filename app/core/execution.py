from app.core.state import engine
from app.data.db import save_trade


def buy(mint, price, size):
    """
    Paper buy:
    - 扣除 capital
    - 建立持倉
    - 記錄 logs / stats
    """
    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": size,
    })

    engine.stats["executed"] += 1
    engine.log(f"BUY {mint[:6]} price={price:.4f} size={size:.6f}")


def sell(pos, price):
    """
    Paper sell:
    - 計算 pnl
    - 回補 capital
    - 寫入 trades.db
    - 記錄 logs
    """
    pnl = (price - pos["entry"]) / pos["entry"]

    engine.capital += pos["size"] * (1 + pnl)

    save_trade(
        pos["mint"],
        pos["entry"],
        price,
        pnl,
    )

    engine.log(
        f"SELL {pos['mint'][:6]} "
        f"entry={pos['entry']:.4f} "
        f"exit={price:.4f} "
        f"pnl={pnl:.4f} "
        f"cap={engine.capital:.4f}"
    )
