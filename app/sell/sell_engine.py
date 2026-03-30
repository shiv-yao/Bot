from config.settings import SETTINGS
positions = []
def add_position(token, entry, wallet):
    positions.append({"token":token,"entry":entry,"peak":entry,"wallet":wallet})
def update_position(token, price):
    for p in positions:
        if p["token"] == token and price > p["peak"]:
            p["peak"] = price
def should_sell(p, price):
    pnl = (price - p["entry"]) / p["entry"]
    dd = (price - p["peak"]) / p["peak"] if p["peak"] else 0.0
    if pnl > SETTINGS["LOCK_PROFIT_TRIGGER"] and dd < SETTINGS["LOCK_PROFIT_DRAWDOWN"]: return True
    if pnl > SETTINGS["TAKE_PROFIT"]: return True
    if pnl < SETTINGS["STOP_LOSS"]: return True
    return False
