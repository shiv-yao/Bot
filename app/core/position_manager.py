TP = 0.05
SL = -0.01
TRAIL = 0.005


def check_exit(pos, price):
    entry = float(pos.get("entry", 0) or 0)
    peak = float(pos.get("peak", entry) or entry)

    if entry <= 0 or price <= 0:
        return None

    pnl = (price - entry) / entry
    dd = (price - peak) / peak if peak > 0 else 0.0

    if pnl >= TP:
        return "TP"

    if pnl <= SL:
        return "SL"

    if pnl > 0.02 and dd < -TRAIL:
        return "TRAIL"

    return None


def manage_position(pos, price):
    """
    相容舊系統：
    回傳 list[tuple[str, float]]
    可讓舊 engine 用：
      for act, ratio in actions:
          ...
    """
    reason = check_exit(pos, price)
    if not reason:
        return []

    if reason == "TP":
        return [("sell_all", 1.0)]
    if reason == "SL":
        return [("sell_all", 1.0)]
    if reason == "TRAIL":
        return [("sell_all", 1.0)]

    return []
