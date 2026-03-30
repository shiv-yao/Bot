import json, os, time

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def _path(name):
    return os.path.join(DATA_DIR, name)

def load_json(name, default):
    try:
        with open(_path(name), "r") as f:
            return json.load(f)
    except:
        return default

def save_json(name, data):
    with open(_path(name), "w") as f:
        json.dump(data, f, indent=2)

# ================= STATE =================

def load_positions():
    return load_json("positions.json", [])

def save_positions(p):
    save_json("positions.json", p)

def load_trades():
    return load_json("trades.json", [])

def append_trade(t):
    trades = load_trades()
    trades.append(t)
    save_json("trades.json", trades)

def load_state():
    return load_json("state.json", {
        "equity": 0,
        "peak": 0,
        "dd": 0
    })

def save_state(s):
    save_json("state.json", s)
