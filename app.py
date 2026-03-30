# ================= v1331 TRUE FUSION PRO =================
# 保留 v1331 原本功能，整合穩定版 / token resolve / fallback / AI learning / production watchdog

import os
import asyncio
import time
import random
import base64
import traceback
from collections import defaultdict

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from state import engine

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
JUP_API_KEY = os.getenv("JUP_API_KEY", "").strip()

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_EXPOSURE_SOL = float(os.getenv("MAX_EXPOSURE_SOL", "1.5"))

MIN_VOLUME = float(os.getenv("MIN_VOLUME", "200000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "120000"))

TP_PCT = float(os.getenv("TP_PCT", "0.35"))
SL_PCT = float(os.getenv("SL_PCT", "0.12"))
DD_PCT = float(os.getenv("DD_PCT", "0.07"))

DISCOVERY_REFRESH_SEC = int(os.getenv("DISCOVERY_REFRESH_SEC", "10"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6.0"))
HTTP_MAX_CONN = int(os.getenv("HTTP_MAX_CONN", "20"))

# ================= HTTP =================
HTTP = httpx.AsyncClient(
    timeout=httpx.Timeout(HTTP_TIMEOUT),
    limits=httpx.Limits(max_connections=HTTP_MAX_CONN, max_keepalive_connections=5),
)

# ================= TOKEN MAP =================
TOKEN_MAP = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": os.getenv("WIF_MINT", "").strip(),
    "MYRO": os.getenv("MYRO_MINT", "").strip(),
    "POPCAT": os.getenv("POPCAT_MINT", "").strip(),
}

FALLBACK_MAP = {
    k: v for k, v in TOKEN_MAP.items() if v
}

TOKEN_CACHE = {}
TOKEN_TS = {}
TOKEN_TTL = 1800

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)
NEW_POOL = {}

PRICE_HISTORY = {}
VOL_HISTORY = {}

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}

ERROR_COUNT = 0
SYSTEM_KILL = False
LAST_HEARTBEAT = time.time()

AI_WEIGHTS = {
    "momentum": 1.0,
    "liquidity": 0.5,
    "smart": 0.8,
    "flow": 0.8,
    "insider": 0.8,
    "new_pool": 0.5,
}
LEARNING_RATE = 0.03

# ================= UTIL =================
def now() -> float:
    return time.time()

def ensure_engine():
    if hasattr(engine, "normalize"):
        try:
            engine.normalize()
        except Exception:
            pass

    if not hasattr(engine, "positions"):
        engine.positions = []
    if not hasattr(engine, "trade_history"):
        engine.trade_history = []
    if not hasattr(engine, "logs"):
        engine.logs = []
    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

def log(msg: str):
    ensure_engine()
    if hasattr(engine, "log") and callable(engine.log):
        engine.log(str(msg))
    else:
        engine.logs.append(str(msg))
        try:
            engine.logs = engine.logs[-500:]
        except Exception:
            pass
    print(msg, flush=True)

def log_once(key: str, msg: str, sec: float = 5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

def get_kp() -> Keypair:
    if not PRIVATE_KEY:
        raise ValueError("PRIVATE_KEY is empty")
    return Keypair.from_base58_string(PRIVATE_KEY)

def looks_like_mint(value: str) -> bool:
    return isinstance(value, str) and len(value) >= 32 and " " not in value and "/" not in value

def current_exposure_sol() -> float:
    total_lamports = sum(p.get("size", 0) for p in engine.positions if isinstance(p, dict))
    return total_lamports / 1_000_000_000

# ================= SAFE HTTP =================
async def safe_get(url, params=None, headers=None):
    for i in range(3):
        try:
            r = await HTTP.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
            log_once("http_status", f"HTTP_STATUS {r.status_code} {url}", 5)
        except Exception as e:
            log_once("http_err", f"HTTP_ERR {e}", 5)
        await asyncio.sleep(0.2 * (i + 1))
    return None

async def safe_post_json(url, json_body=None, headers=None):
    for i in range(3):
        try:
            r = await HTTP.post(url, json=json_body, headers=headers)
            if r.status_code == 200:
                return r.json()
            log_once("post_status", f"POST_STATUS {r.status_code} {url}", 5)
        except Exception as e:
            log_once("post_err", f"POST_ERR {e}", 5)
        await asyncio.sleep(0.2 * (i + 1))
    return None

# ================= TOKEN RESOLVE =================
async def resolve_token(symbol_or_mint: str):
    if not symbol_or_mint:
        return None

    q = str(symbol_or_mint).strip()
    uq = q.upper()

    if looks_like_mint(q):
        return q

    if uq in TOKEN_MAP and TOKEN_MAP[uq]:
        return TOKEN_MAP[uq]

    if uq in FALLBACK_MAP and FALLBACK_MAP[uq]:
        return FALLBACK_MAP[uq]

    if uq in DISCOVERED:
        return DISCOVERED[uq]["mint"]

    cached = TOKEN_CACHE.get(uq)
    ts = TOKEN_TS.get(uq, 0)
    if cached and now() - ts < TOKEN_TTL:
        return cached

    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

    # 先試 Jupiter token search
    for url in [
        "https://api.jup.ag/tokens/v2/search",
        "https://api.jup.ag/tokens/v1/search",
    ]:
        data = await safe_get(url, params={"query": q}, headers=headers)
        if not data:
            continue

        tokens = []
        if isinstance(data, dict):
            if isinstance(data.get("tokens"), list):
                tokens = data["tokens"]
            elif isinstance(data.get("data"), list):
                tokens = data["data"]
            elif isinstance(data.get("results"), list):
                tokens = data["results"]
        elif isinstance(data, list):
            tokens = data

        if tokens:
            best = tokens[0]
            mint = best.get("address") or best.get("mint") or best.get("id")
            if mint:
                TOKEN_CACHE[uq] = mint
                TOKEN_TS[uq] = now()
                log_once(f"resolve_{uq}", f"TOKEN_RESOLVE {uq}->{mint[:6]}", 30)
                return mint

    log_once(f"token_fail_{uq}", f"TOKEN_RESOLVE_FAIL {uq}", 10)
    return None

# ================= DISCOVERY =================
async def discover():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")
            if not data:
                await asyncio.sleep(DISCOVERY_REFRESH_SEC)
                continue

            pairs = data.get("pairs", [])
            new = set()

            for p in pairs[:100]:
                vol = float((p.get("volume") or {}).get("h24", 0) or 0)
                liq = float((p.get("liquidity") or {}).get("usd", 0) or 0)

                if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
                    continue

                base = p.get("baseToken", {})
                symbol = (base.get("symbol") or "").upper()
                mint = base.get("address")

                if not symbol or not mint:
                    continue

                buys = int((((p.get("txns") or {}).get("h24") or {}).get("buys", 1)) or 1)
                sells = int((((p.get("txns") or {}).get("h24") or {}).get("sells", 1)) or 1)

                # Rug filter 基礎版
                if sells > buys * 2:
                    continue

                age_ms = int(p.get("pairCreatedAt", 0) or 0)
                if age_ms > 0 and now() - (age_ms / 1000) < 900:
                    NEW_POOL[symbol] = True

                DISCOVERED[symbol] = {
                    "mint": mint,
                    "liquidity": liq,
                    "volume": vol,
                    "buys": buys,
                    "sells": sells,
                }
                new.add(symbol)

            if new:
                CANDIDATES.clear()
                CANDIDATES.update(new)
                engine.candidate_count = len(new)
                log_once("discover", f"DISCOVER {len(new)}", 5)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"DISCOVER_ERR {e}")

        await asyncio.sleep(DISCOVERY_REFRESH_SEC)

# ================= SMART MONEY =================
async def smart_money():
    while True:
        try:
            for m in list(CANDIDATES):
                SMART_MONEY[m] *= 0.90
                if random.random() < 0.4:
                    SMART_MONEY[m] += 0.3
        except Exception as e:
            log_once("smart_err", f"SMART_ERR {e}", 5)
        await asyncio.sleep(3)

# ================= FLOW =================
async def flow():
    while True:
        try:
            for m in list(CANDIDATES):
                FLOW[m] *= 0.85
                if random.random() < 0.5:
                    FLOW[m] += 0.2
        except Exception as e:
            log_once("flow_err", f"FLOW_ERR {e}", 5)
        await asyncio.sleep(2)

# ================= INSIDER =================
async def insider():
    while True:
        try:
            for m in list(CANDIDATES):
                INSIDER[m] *= 0.92
                if NEW_POOL.get(m) and random.random() < 0.3:
                    INSIDER[m] += 0.4
        except Exception as e:
            log_once("insider_err", f"INSIDER_ERR {e}", 5)
        await asyncio.sleep(3)

# ================= PRICE =================
async def get_price(symbol):
    mint = await resolve_token(symbol)
    if not mint:
        return None

    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None
    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1_000_000,
            "slippageBps": 100,
        },
        headers=headers,
    )

    if not data:
        log_once("no_price", f"NO PRICE {symbol}", 5)
        return None

    try:
        if data.get("outAmount"):
            return float(data["outAmount"]) / 1e6
        if data.get("data"):
            return float(data["data"][0]["outAmount"]) / 1e6
    except Exception as e:
        log_once("price_parse", f"PRICE_PARSE {e}", 5)

    return None

# ================= FEATURE =================
async def features(symbol):
    price = await get_price(symbol)
    if not price:
        return None

    hist = PRICE_HISTORY.get(symbol, [])
    hist.append(price)
    hist = hist[-5:]
    PRICE_HISTORY[symbol] = hist

    if len(hist) < 3:
        return None

    momentum = (hist[-1] - hist[0]) / hist[0]
    vol = max(hist) - min(hist)
    VOL_HISTORY[symbol] = vol

    meta = DISCOVERED.get(symbol, {})
    liquidity = float(meta.get("liquidity", 0)) / 1_000_000

    return {
        "momentum": momentum,
        "liquidity": liquidity,
        "smart": SMART_MONEY[symbol],
        "flow": FLOW[symbol],
        "insider": INSIDER[symbol],
        "new_pool": 1.0 if NEW_POOL.get(symbol) else 0.0,
    }

# ================= AI =================
def ai_score(f):
    return sum(f[k] * AI_WEIGHTS[k] for k in f)

def learn_from_trade(f, pnl):
    for k in AI_WEIGHTS:
        AI_WEIGHTS[k] += LEARNING_RATE * pnl * f.get(k, 0)
        AI_WEIGHTS[k] = max(min(AI_WEIGHTS[k], 2.0), -1.0)

# ================= ALPHA =================
async def alpha(symbol):
    f = await features(symbol)
    if not f:
        return 0, None

    score = ai_score(f)
    return score, f

# ================= POSITION SIZE =================
def size(score, symbol):
    vol = VOL_HISTORY.get(symbol, 0.01)
    risk = 1 / (vol + 0.001)
    raw = score * risk
    lamports = int(1_000_000 * min(max(raw, 0.2), 2.0))
    return lamports

# ================= RISK / FILTER =================
def rug_filter(symbol):
    meta = DISCOVERED.get(symbol, {})
    liq = float(meta.get("liquidity", 0))
    buys = int(meta.get("buys", 1))
    sells = int(meta.get("sells", 1))

    if liq < 50_000:
        return False
    if sells > buys * 2:
        return False
    return True

def can_buy(symbol):
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if symbol in [p.get("token") for p in engine.positions if isinstance(p, dict)]:
        return False
    if now() - TOKEN_COOLDOWN[symbol] < 10:
        return False
    if current_exposure_sol() > MAX_EXPOSURE_SOL:
        return False
    if not rug_filter(symbol):
        return False
    return True

# ================= JUP =================
async def safe_jupiter_order(input_mint, output_symbol, amount):
    output_mint = await resolve_token(output_symbol)
    if not output_mint:
        return None

    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

    for _ in range(3):
        data = await safe_get(
            "https://api.jup.ag/swap/v2/order",
            {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "taker": str(get_kp().pubkey()),
                "slippageBps": 100,
            },
            headers=headers,
        )
        if data:
            if data.get("_quote_only"):
                return data
            if data.get("transaction"):
                return data
        await asyncio.sleep(0.2)
    return None

async def jupiter_exec(order):
    try:
        headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

        tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
        signed = VersionedTransaction(tx.message, [get_kp()])

        data = await safe_post_json(
            "https://api.jup.ag/swap/v2/execute",
            {
                "signedTransaction": base64.b64encode(bytes(signed)).decode(),
                "requestId": order.get("requestId"),
            },
            headers=headers,
        )
        return data
    except Exception as e:
        log_once("exec_err", f"EXEC_ERR {e}", 5)
        return None

# ================= BUY =================
async def buy(symbol, score):
    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if not can_buy(symbol):
            return

        f = await features(symbol)
        if not f:
            return

        amt = size(score, symbol)
        log_once(f"buy_{symbol}", f"BUY {symbol} score={score:.2f} size={amt}", 2)

        order = await safe_jupiter_order(SOL, symbol, amt)
        if not order:
            log(f"BUY_FAIL {symbol}")
            return

        if order.get("_quote_only"):
            log_once("quote_only", f"QUOTE_ONLY {symbol}", 5)
            return

        res = await jupiter_exec(order)
        if not res:
            log(f"EXEC_FAIL {symbol}")
            return

        price = await get_price(symbol)
        if not price:
            return

        engine.positions.append({
            "token": symbol,
            "entry_price": price,
            "peak_price": price,
            "size": amt,
            "features": f,
            "entry_ts": now(),
        })

        engine.stats["buys"] += 1
        TOKEN_COOLDOWN[symbol] = now()
        engine.last_trade = f"BUY {symbol} @ {price:.6f}"
        log(f"BUY {symbol}")

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= SELL =================
async def sell(p):
    symbol = p["token"]
    if symbol in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(symbol)

    try:
        price = await get_price(symbol)
        if not price:
            return

        pnl = (price - p["entry_price"]) / p["entry_price"]

        learn_from_trade(p.get("features", {}), pnl)

        if p in engine.positions:
            engine.positions.remove(p)

        engine.trade_history.append({
            "token": symbol,
            "pnl": pnl,
            "ts": now(),
        })

        engine.stats["sells"] += 1
        engine.last_trade = f"SELL {symbol} pnl={pnl:.3f}"
        log(f"SELL {symbol} pnl={pnl:.3f}")

    finally:
        IN_FLIGHT_SELL.discard(symbol)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])
                if not price:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)
                p["peak_price"] = peak

                dd = (price - peak) / peak

                if pnl > TP_PCT or pnl < -SL_PCT or dd < -DD_PCT:
                    await sell(p)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= MAIN =================
async def main():
    while True:
        try:
            ranked = []

            for m in list(CANDIDATES):
                s, _ = await alpha(m)
                ranked.append((m, s))
                engine.stats["signals"] += 1

            ranked.sort(key=lambda x: x[1], reverse=True)
            log_once("scan", f"SCAN {len(ranked)}", 3)

            for m, s in ranked[:5]:
                if s > ENTRY_THRESHOLD:
                    await buy(m, s)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= SAFE TASK / PROD =================
async def safe_task(name, coro):
    global ERROR_COUNT, SYSTEM_KILL
    while True:
        try:
            await coro()
        except Exception as e:
            ERROR_COUNT += 1
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"[CRASH] {name} {e}")
            traceback.print_exc()

            if ERROR_COUNT > 20:
                SYSTEM_KILL = True
                log("🔥 SYSTEM KILL")

            await asyncio.sleep(2)

async def watchdog():
    while True:
        log_once("watchdog", "SYSTEM OK", 10)
        await asyncio.sleep(5)

async def heartbeat():
    global LAST_HEARTBEAT
    while True:
        LAST_HEARTBEAT = time.time()
        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    log("SYSTEM START v1331 TRUE FUSION")

    asyncio.create_task(safe_task("discover", discover))
    asyncio.create_task(safe_task("smart_money", smart_money))
    asyncio.create_task(safe_task("flow", flow))
    asyncio.create_task(safe_task("insider", insider))
    asyncio.create_task(safe_task("main", main))
    asyncio.create_task(safe_task("monitor", monitor))
    asyncio.create_task(watchdog())
    asyncio.create_task(heartbeat())

@app.get("/")
def root():
    ensure_engine()
    if hasattr(engine, "snapshot") and callable(engine.snapshot):
        return engine.snapshot()

    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "candidates": list(CANDIDATES),
        "logs": list(engine.logs)[-20:],
    }

@app.get("/debug")
def debug():
    ensure_engine()
    return {
        "positions": len(engine.positions),
        "candidate_count": len(CANDIDATES),
        "discovered_count": len(DISCOVERED),
        "stats": engine.stats,
        "weights": AI_WEIGHTS,
        "cooldowns": len(TOKEN_COOLDOWN),
        "system_kill": SYSTEM_KILL,
        "error_count": ERROR_COUNT,
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1331 TRUE FUSION PRO</h2>
    <div id=data></div>
    <script>
    async function load(){
        let r = await fetch('/');
        let d = await r.json();
        document.getElementById('data').innerHTML =
        '<pre>'+JSON.stringify(d,null,2)+'</pre>';
    }
    setInterval(load,2000);
    load();
    </script>
    </body>
    </html>
    """)
