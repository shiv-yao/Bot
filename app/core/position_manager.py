def manage_position(pos, price):
    entry = float(pos.get("entry", 0.0) or 0.0)
    peak = float(pos.get("peak", 0.0) or 0.0)

    if entry <= 0 or price <= 0:
        return []

    pnl = (price - entry) / entry
    drawdown = (price - peak) / peak if peak > 0 else 0.0

    actions = []

    # 1) break-even arm
    if pnl > 0.02 and pos.get("breakeven_armed") is not True:
        pos["breakeven_armed"] = True
        pos["stop_price"] = entry
        actions.append(("breakeven", 0.0))

    # 2) partial TP
    if pnl > 0.04 and not pos.get("tp1_done"):
        pos["tp1_done"] = True
        actions.append(("partial_sell", 0.5))

    # 3) add winner
    if pnl > 0.03 and pos.get("add_done") is not True:
        pos["add_done"] = True
        actions.append(("add", 0.5))

    # 4) break-even exit
    stop_price = pos.get("stop_price")
    if pos.get("breakeven_armed") and stop_price is not None:
        if price <= stop_price:
            actions.append(("sell_all", 1.0))

    # 5) trailing exit
    if pnl > 0.015 and drawdown < -0.005:
        actions.append(("sell_all", 1.0))

    # 6) time stop
    if pos.get("time") and (pos.get("time_age", 0) > 60):
        actions.append(("sell_all", 1.0))

    return actions
