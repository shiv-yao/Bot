from app.core.state import engine

BASE_SIZE = 0.01


def get_position_size(score, wallet_alpha, insider, regime):
    size = BASE_SIZE

    # ===== 1️⃣ 基礎分數放大 =====
    size *= (1 + score * 2)

    # ===== 2️⃣ 聰明錢 =====
    if wallet_alpha > 0.6:
        size *= 2
    elif wallet_alpha > 0.3:
        size *= 1.3

    # ===== 3️⃣ insider =====
    if insider > 0.4:
        size *= 1.5

    # ===== 4️⃣ regime =====
    if regime == "trend_up":
        size *= 1.2
    elif regime == "flat":
        size *= 0.7
    elif regime == "trend_down":
        size *= 0.4

    # ===== 5️⃣ 連勝加碼 =====
    wins = engine.stats.get("wins", 0)
    losses = engine.stats.get("losses", 0)

    total = wins + losses
    winrate = wins / total if total > 0 else 0.5

    if winrate > 0.6:
        size *= 1.3
    elif winrate < 0.4:
        size *= 0.7

    # ===== 6️⃣ drawdown 控制 =====
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital

        if dd < -0.15:
            size *= 0.3
        elif dd < -0.08:
            size *= 0.5

    # ===== 7️⃣ 資金限制 =====
    max_cap = engine.capital * 0.25
    size = min(size, max_cap)

    return max(size, 0.001)
