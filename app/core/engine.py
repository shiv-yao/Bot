# V28 PRO + V29 MONEY MODE

import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

try:
from app.sources.fusion import fetch_candidates
except Exception:
async def fetch_candidates():
return []

try:
from app.data.market import get_quote, looks_like_solana_mint
except Exception:
async def get_quote(a, b, c):
return None
def looks_like_solana_mint(x):
return True

try:
from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
async def update_token_wallets(m):
return []

MAX_POSITIONS = 3
MAX_EXPOSURE = 0.45
MAX_POSITION_SIZE = 0.18

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 45

TOKEN_COOLDOWN = 12

SOL = “So11111111111111111111111111111111111111112”
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

def ensure_engine():
engine.positions = getattr(engine, “positions”, [])
engine.trade_history = getattr(engine, “trade_history”, [])
engine.logs = getattr(engine, “logs”, [])

```
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
```

def log(msg):
print(msg)
engine.logs.append(str(msg))
engine.logs = engine.logs[-200:]

def sf(x):
try:
return float(x)
except Exception:
return 0.0

def risk():
if engine.peak_capital > 0:
dd = (engine.capital - engine.peak_capital) / engine.peak_capital
if dd < -0.30:
log(“HARD STOP”)
engine.running = False
return False
return True

def exposure():
return sum(sf(p.get(“size”, 0)) for p in engine.positions)

async def safe_quote(input_mint, output_mint, amount):
try:
q = await get_quote(input_mint, output_mint, amount)
if q:
return q
except Exception as e:
log(“QUOTE ERR “ + str(e)[:60])
return None

async def get_price(m):
if not looks_like_solana_mint(m):
return None

```
q = await safe_quote(SOL, m, AMOUNT)
if not q or not q.get("outAmount"):
    return None

out = sf(q["outAmount"])
if out <= 0:
    return None

return out / 1e6, q
```

async def features(t):
mint = t[“mint”]
source = t.get(“source”, “unknown”)

```
if not looks_like_solana_mint(mint):
    return None

try:
    wallets = await update_token_wallets(mint)
except Exception:
    wallets = []

data = await get_price(mint)
if not data:
    return None

price, q = data

prev = LAST_PRICE.get(mint)
breakout = 0.0
if prev:
    breakout = max((price - prev) / prev, 0)

LAST_PRICE[mint] = price

liq = sf(q.get("outAmount", 0)) / 1e5

source_bonus = {
    "pump": 1.2,
    "dex": 1.0,
    "dex_boost": 1.1,
    "helius": 1.05,
    "jup": 0.85,
}.get(source, 1.0)

breakout *= source_bonus

if prev is not None and breakout < 0.01:
    return None
if liq < 0.002:
    return None
if len(wallets) < 1:
    return None

return {
    "mint": mint,
    "source": source,
    "breakout": breakout,
    "smart_money": min(len(wallets) / 8, 1),
    "liquidity": liq,
    "insider": 0.05,
    "wallet_count": len(wallets),
    "price": price,
}
```

def size(score):
base = engine.capital * 0.08
if score > 0.4:
base *= 1.3
return min(base, engine.capital * MAX_POSITION_SIZE)

async def check_sell(p):
data = await get_price(p[“mint”])
if not data:
return

```
price, _ = data
entry = p["entry"]
pnl = (price - entry) / entry
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

engine.trade_history.append({
    "mint": p["mint"],
    "pnl": pnl,
    "reason": reason,
    "timestamp": time.time(),
    "meta": {"source": p.get("source")},
})

if pnl > 0:
    engine.stats["wins"] += 1
else:
    engine.stats["losses"] += 1

if engine.capital > engine.peak_capital:
    engine.peak_capital = engine.capital

log("SELL " + p["mint"][:6] + " " + reason + " pnl=" + str(round(pnl, 4)))
```

async def trade(t):
mint = t[“mint”]

```
if not looks_like_solana_mint(mint):
    log("BAD_MINT " + mint)
    return False

if any(p["mint"] == mint for p in engine.positions):
    return False

now = time.time()

if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
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
    "time": now,
    "peak": 0.0,
    "source": f["source"],
})

LAST_TRADE[mint] = now

engine.stats["signals"] += 1
engine.stats["executed"] += 1

log("BUY " + mint[:6] + " src=" + f["source"] + " score=" + str(round(score, 3)))
return True
```

async def main_loop():
ensure_engine()
log(“V28 PRO + V29 MONEY MODE START”)

```
while engine.running:
    traded = False

    try:
        if not risk():
            break

        tokens = await fetch_candidates()

        for t in tokens:
            if await trade(t):
                traded = True

        for p in list(engine.positions):
            await check_sell(p)

        if not traded:
            engine.no_trade_cycles += 1
        else:
            engine.no_trade_cycles = 0

    except Exception as e:
        engine.stats["errors"] += 1
        log("ERR " + str(e))

    await asyncio.sleep(2)
```
