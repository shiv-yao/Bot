import asyncio
import time
import random
import httpx

# ================= CONFIG =================

BASE_SIZE = 0.01
MAX_POSITIONS = 3

ENTRY_THRESHOLD = 0.02

TP = 0.04
SL = -0.025
TRAIL = 0.02

COOLDOWN = 60

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

            for p in data.get("pairs", []):
                vol = p.get("volume", {}).get("h24", 0)
                change = p.get("priceChange", {}).get("h1", 0)

                # 🔥 過濾垃圾幣
                if vol < 50000:
                    continue

                if abs(change) < 2:
                    continue

                tokens.append({
                    "mint": p["baseToken"]["address"],
                    "volume": vol,
                    "change": change
                })

                if len(tokens) >= 15:
                    break

            return tokens

    except Exception as e:
        log(f"DEX_ERROR {e}")
        return []

# ================= PRICE =================

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

# ================= TRUE ALPHA =================

def compute_score(volume, change):
    mom = change / 100           # 動能
    vol = min(volume / 1e6, 1)   # 流動性
    noise = random.random() * 0.003

    score = mom * 0.6 + vol * 0.3 + noise

    return score

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

    log(f"BUY {mint[:6]} score={score:.4f} price={price:.4f}")

# ================= SELL =================

def sell(pos, price, reason):
    global capital

    pnl = (price - pos["entry"]) / pos["entry"]

    capital += pos["size"] * (1 + pnl)

    log(f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f}")

# ================= POSITION =================

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

# ================= MAIN =================

async def main_loop():
    global stats

    while True:
        try:
            tokens = await fetch_candidates()

            prices = {}

            # 抓價格
            for t in tokens:
                price = await get_price(t["mint"])
                if price:
                    prices[t["mint"]] = price

            # 管理持倉
            manage_positions(prices)

            # 掃描
            for t in tokens:
                stats["signals"] += 1

                mint = t["mint"]

                if mint in cooldown_map and time.time() - cooldown_map[mint] < COOLDOWN:
                    log(f"COOLDOWN {mint[:6]}")
                    continue

                price = prices.get(mint)
                if not price:
                    continue

                score = compute_score(t["volume"], t["change"])

                log(f"SCORE {mint[:6]} {score:.4f}")

                # 🔥 Fake pump 過濾
                if t["change"] > 25:
                    log(f"SKIP_PUMP {mint[:6]}")
                    continue

                if score < ENTRY_THRESHOLD:
                    stats["rejected"] += 1
                    log(f"REJECT {mint[:6]}")
                    continue

                buy(mint, price, score)

                cooldown_map[mint] = time.time()

        except Exception as e:
            stats["errors"] += 1
            log(f"ERROR {e}")

        await asyncio.sleep(6)
