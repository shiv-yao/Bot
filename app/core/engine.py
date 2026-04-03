# ================= V27.1 + INFRA FINAL =================
import asyncio
import time
from collections import defaultdict
import httpx

from app.state import engine
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

# ===== DATA =====
try:
    from app.sources.fusion import fetch_candidates as raw_fetch
except:
    async def raw_fetch():
        return []

try:
    from app.data.market import looks_like_solana_mint
except:
    def looks_like_solana_mint(x):
        return True

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m):
        return []

# ===== CONFIG =====
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.45
MAX_POSITION_SIZE = 0.18

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 45

TOKEN_COOLDOWN = 12

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# ===== INFRA =====
JUP_ENDPOINTS = [
    "https://quote-api.jup.ag/v6/quote",
    "https://lite-api.jup.ag/swap/v1/quote",
]

HEADERS = {"User-Agent": "Mozilla/5.0"}

LAST_FETCH = 0

# ===== INIT =====
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
        "rejected": 0,
    })

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]

def sf(x):
    try:
        return float(x)
    except:
        return 0.0

# ===== NETWORK =====
async def safe_get(url, params=None):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url, params=params, headers=HEADERS)
            return r
    except Exception as e:
        log(f"HTTP ERR {str(e)[:40]}")
        return None

async def safe_quote(input_mint, output_mint, amount):
    for url in JUP_ENDPOINTS:
        r = await safe_get(url, {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 50
        })

        if not r:
            continue

        if r.status_code == 200:
            try:
                data = r.json()
                if "outAmount" in data:
                    return data
            except:
                pass

        log(f"JUP FAIL {r.status_code}")
        await asyncio.sleep(0.3)

    return None

async def fetch_candidates():
    global LAST_FETCH

    if time.time() - LAST_FETCH < 2:
        return []

    LAST_FETCH = time.time()

    try:
        return await raw_fetch()
    except Exception as e:
        log(f"FETCH ERR {e}")
        return []

# ===== RISK =====
def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.30:
            log("🛑 HARD STOP")
            engine.running = False
            return False
    return True

def exposure():
    return sum(sf(p.get("size", 0)) for p in engine.positions)

# ===== PRICE =====
async def get_price(m):
    if not looks_like_solana_mint(m):
        return None

    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount"))
    if out <= 0:
        return None

    return out / 1e6, q

# ===== FEATURES =====
async def features(t):
    mint = t["mint"]
    source = t.get("source", "unknown")

    if not looks_like_solana_mint(mint):
        return None

    try:
        wallets = await update_token_wallets(mint)
    except:
        wallets = []

    data = await get_price(mint)
    if not data:
        return None

    price, q = data

    prev = LAST_PRICE.get(mint)
    breakout = max((price - prev) / prev, 0) if prev else 0

    LAST_PRICE[mint] = price

    liq = sf(q.get("outAmount")) / 1e5

    if breakout < 0.01:
        return None
    if liq < 0.002:
        return None
    if len(wallets) < 2:
        return None

    return {
        "mint": mint,
        "source": source,
        "breakout": breakout,
        "smart_money": min(len(wallets)/8, 1),
        "liquidity": liq,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price": price,
    }

# ===== SIZE =====
def size(score):
    base = engine.capital * 0.08
    if score > 0.4:
        base *= 1.3
    return min(base, engine.capital * MAX_POSITION_SIZE)

# ===== EXIT =====
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return

    price, _ = data
    pnl = (price - p["entry"]) / p["entry"]
    held = time.time() - p["time"]

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak", 0) - TRAILING_GAP:
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC and pnl < 0.01:
        reason = "TIME"

    if pnl > p.get("peak", 0):
        p["peak"] = pnl

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1 + pnl)

    if pnl > 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")

# ===== TRADE =====
async def trade(t):
    mint = t["mint"]

    if any(p["mint"] == mint for p in engine.positions):
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    f = await features(t)
    if not f:
        return False

    ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    if not ok:
        return False

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        "unknown",
        {},
        {},
    )

    min_score = 0.22 if f["source"] == "pump" else 0.27
    if score < min_score:
        return False

    s = size(score)
    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": s,
        "time": time.time(),
        "peak": 0.0,
        "source": f["source"]
    })

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {mint[:6]} src={f['source']} score={score:.3f}")
    return True

# ===== MAIN =====
async def main_loop():
    ensure_engine()
    log("🔥 V27.1 INFRA FINAL START")

    while engine.running:
        try:
            if not risk():
                break

            tokens = await fetch_candidates()

            for t in tokens:
                await trade(t)

            for p in list(engine.positions):
                await check_sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
