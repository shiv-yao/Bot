import asyncio
import random
import httpx
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
    "candidates": [],
    "last_execution": None,
    "realized_pnl": 0.0,
    "daily_pnl": 0.0,
    "daily_trades": 0,
    "last_reset": time.time(),
    "scanner_mode": None,
    "scanner_error": None,
    "last_alpha": None,
    "bot_version": "alpha_dual_engine_v5_allocator",
    "candidate_count": 0,
    "loss_streak": 0,
    "engine_stats": {
        "stable": {
            "pnl": 0.0,
            "trades": 0,
            "wins": 0,
            "winrate": 0.0,
            "open_positions": 0,
        },
        "degen": {
            "pnl": 0.0,
            "trades": 0,
            "wins": 0,
            "winrate": 0.0,
            "open_positions": 0,
        },
    },
}

# ================= CONFIG =================

MAX_POSITIONS = 4
MAX_DAILY_TRADES = 20
MAX_HOLD_SECONDS = 120

STOP_LOSS = -0.06
TAKE_PROFIT = 0.12
DAILY_STOP = -0.03

GAS_COST = 0.000005

# ================= HELPERS =================

def has_position(mint: str) -> bool:
    return any(p["token"] == mint for p in STATE["positions"])


def is_valid_mint(mint: str) -> bool:
    if not mint:
        return False
    if len(mint) < 32 or len(mint) > 44:
        return False
    if any(c in mint for c in [".", "/", ":"]):
        return False
    if mint.startswith("0x"):
        return False
    return True


# ================= AI ALLOCATOR =================

def get_engine_weight(engine: str) -> float:
    stats = STATE["engine_stats"][engine]
    weight = 1.0

    if stats["trades"] >= 3:
        if stats["winrate"] > 0.6:
            weight *= 1.3
        elif stats["winrate"] < 0.4:
            weight *= 0.7

        if stats["pnl
