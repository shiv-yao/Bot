# app/core/state.py

positions = []
cooldown = {}

capital = 1.0

stats = {
    "signals": 0,
    "executed": 0,
    "rejected": 0,
    "errors": 0
}

logs = []

MODE = "PAPER"   # 🔥 PAPER / REAL
