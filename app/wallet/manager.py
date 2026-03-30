import os, json
from app.core.state import engine

def load_wallets():
    raw = os.getenv("WALLETS_JSON", '{"main":{"enabled":true,"weight":1.0}}')
    try:
        wallets = json.loads(raw)
    except Exception:
        wallets = {"main":{"enabled":True,"weight":1.0}}
    norm = {}
    for name, cfg in wallets.items():
        norm[name] = {"enabled": bool(cfg.get("enabled", True)), "weight": float(cfg.get("weight", 1.0))}
    engine.wallets = norm
    return norm

def active_wallets():
    wallets = engine.wallets or load_wallets()
    return {k:v for k,v in wallets.items() if v.get("enabled", True)}

def wallet_scale():
    wallets = active_wallets()
    total = sum(v["weight"] for v in wallets.values()) or 1.0
    return {k:v["weight"]/total for k,v in wallets.items()}
