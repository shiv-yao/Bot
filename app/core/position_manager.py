def manage_position(pos, price):
    entry = pos["entry"]
    peak = pos["peak"]
    size = pos["size"]

    pnl = (price - entry) / entry
    drawdown = (price - peak) / peak if peak > 0 else 0

    actions = []

    # 1️⃣ Break-even（保本）
    if pnl > 0.02 and pos.get("breakeven") is not True:
        pos["breakeven"] = True
        actions.append(("breakeven", 0.0))

    # 2️⃣ Partial TP（先收一半）
    if pnl > 0.04 and not pos.get("tp1_done"):
        pos["tp1_done"] = True
        actions.append(("partial_sell", 0.5))

    # 3️⃣ Add winner（加碼強單）
    if pnl > 0.03 and pos.get("add_done") is not True:
        pos["add_done"] = True
        actions.append(("add", 0.5))

    # 4️⃣ Trailing stop（追蹤停利）
    if pnl > 0.015 and drawdown < -0.005:
        actions.append(("sell_all", 1.0))

    # 5️⃣ Time stop（拖太久）
    if pos.get("time") and (pos.get("time_age", 0) > 60):
        actions.append(("sell_all", 1.0))

    return actions
