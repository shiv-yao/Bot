import asyncio
import time
import random
import httpx

# ================= CONFIG =================

BASE_SIZE = 0.01
MAX_POSITIONS = 4

ENTRY_THRESHOLD = 0.015

TP = 0.03
SL = -0.02
TRAIL = 0.015

COOLDOWN = 30

JUP_URL = "https://quote-api.jup.ag/v6/quote"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/solana"

# ================= STATE =================

positions = []
cooldown_map = {}
capital = 1.0

stats = {
    "signals": 0,
    "executed": 0,
    "rejected": 0,
    "errors": 0
}

logs = []

# ================= LOG =================

def log(msg):
    print(msg)
    logs.append(msg)
    if len(logs) > 200:
        logs.pop(0)

# ================= FETCH TOKENS =================

async def fetch_candidates():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(DEX_URL)
            data = r.json()

            tokens = []
            for t in data.get("pairs", [])[:20]:
                tokens.append(t["baseToken"]["address"])

            return tokens

    except Exception as e:
        log(f"DEX_ERROR {e}")
        return []

# ================= PRICE (REAL JUP) =================

async def get_price(mint):
    try:
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": mint,
            "amount": 1000000,
            "slippageBps": 50
        }

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(JUP_URL, params=params)
            j = r.json()

            routes = j.get("data", [])
            if not routes:
                return None

            out = int(routes[0]["outAmount"])
            return out / 1000000

    except Exception as e:
        log(f"PRICE_ERROR {e}")
        return None

# ================= ALPHA =================

def compute_score(price):
    if not price:
        return 0
    return min(price * 0.01, 0.05)

# ================= BUY =================

def buy(mint, price, score):
    global capital

    if len(positions) >= MAX_POSITIONS:
        log("MAX_POSITIONS")
        return

    if capital <= 0:
        return

    size = BASE_SIZE

    positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": size,
        "time": time.time()
    })

    capital -= size

    stats["executed"] += 1

    log(f"BUY {mint[:6]} price={price:.4f}")

# ================= SELL =================

def sell(pos, price, reason):
    global capital

    pnl = (price - pos["entry"]) / pos["entry"]

    capital += pos["size"] * (1 + pnl)

    log(f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f}")

# ================= POSITION MGMT =================

def manage_positions(prices):
    global positions

    new_positions = []

    for p in positions:
        mint = p["mint"]
        price = prices.get(mint)

        if not price:
            new_positions.append(p)
            continue

        pnl = (price - p["entry"]) / p["entry"]

        if price > p["peak"]:
            p["peak"] = price

        dd = (price - p["peak"]) / p["peak"]

        log(f"CHECK {mint[:6]} pnl={pnl:.4f} dd={dd:.4f}")

        if pnl >= TP:
            sell(p, price, "TP")
            continue

        if pnl <= SL:
            sell(p, price, "SL")
            continue

        if dd <= -TRAIL:
            sell(p, price, "TRAIL")
            continue

        new_positions.append(p)

    positions = new_positions

# ================= MAIN LOOP =================

async def main_loop():
    global stats

    while True:
        try:
            tokens = await fetch_candidates()

            prices = {}

            # 抓價格
            for t in tokens:
                price = await get_price(t)
                if price:
                    prices[t] = price

            # 管理持倉
            manage_positions(prices)

            # 掃描買點
            for mint in tokens:
                stats["signals"] += 1

                if mint in cooldown_map and time.time() - cooldown_map[mint] < COOLDOWN:
                    log(f"COOLDOWN {mint[:6]}")
                    continue

                price = prices.get(mint)
                if not price:
                    continue

                score = compute_score(price)

                log(f"SCORE {mint[:6]} {score:.4f}")

                if score < ENTRY_THRESHOLD:
                    stats["rejected"] += 1
                    continue

                buy(mint, price, score)

                cooldown_map[mint] = time.time()

        except Exception as e:
            stats["errors"] += 1
            log(f"ERROR {e}")

        await asyncio.sleep(5)
