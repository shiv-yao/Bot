engine = {
    "running": True,
    "mode": "PAPER",
    "capital": 0.0,
    "sol_balance": 0.0,
    "last_signal": "",
    "last_trade": "",
    "positions": [],
    "logs": [],
    "stats": {
        "signals": 0,
        "buys": 0,
        "sells": 0,
        "errors": 0
    }
}

def log(msg: str):
    engine["logs"].append(msg)
    if len(engine["logs"]) > 300:
        engine["logs"].pop(0)
