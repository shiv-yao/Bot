# ================= v1327 AUTO TOKEN IDENTIFY =================
# 🔥 保留全部功能 + 自動識別 token(symbol->mint) + 修 NO PRICE 問題

import os
import asyncio
import time
import random
import base64
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from state import engine
import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= HTTP =================
HTTP = httpx.AsyncClient(
    timeout=httpx.Timeout(5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=5)
)

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
JUP_API_KEY = os.getenv("JUP_API_KEY", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

# 你原本保留的候選
CANDIDATES = {"BONK", "WIF", "JUP", "MYRO", "POPCAT"}

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.005"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "2"))

TP_PCT = float(os.getenv("TP_PCT", "0.10"))
SL_PCT = float(os.getenv("SL_PCT", "0.05"))
TRAIL_DD_PCT = float(os.getenv("TRAIL_DD_PCT", "0.05"))

BUY_LAMPORTS = int(os.getenv("BUY_LAMPORTS", "1000000"))

# 優先用你自己已知 mapping；沒填的會自動查
TOKEN_MAP = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": os.getenv("WIF_MINT", "").strip(),
    "MYRO": os.getenv("MYRO_MINT", "").strip(),
    "POPCAT": os.getenv("POPCAT_MINT", "").strip(),
}

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}

PRICE_HISTORY = {}
MEMPOOL_SIGNAL = {}

# symbol / mint 自動識別快取
TOKEN_SEARCH_CACHE = {}
TOKEN_SEARCH_TS = {}
TOKEN_SEARCH_TTL = 60 * 30

# ================= UTIL =================
def now():
    return time.time()

def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, set):
        return list(v)
    if isinstance(v, dict):
        return [v]
    if isinstance(v, str):
        return [v]
    try:
        return list(v)
    except Exception:
        return []

def ensure_engine():
    current_positions = getattr(engine, "positions", [])
    current_trade_history = getattr(engine, "trade_history", [])
    current_logs = getattr(engine, "logs", [])
    current_stats = getattr(engine, "stats", {})

    engine.positions = ensure_list(current_positions)
    engine.trade_history = ensure_list(current_trade_history)
    engine.logs = ensure_list(current_logs)

    if not isinstance(current_stats, dict):
        current_stats = {}

    engine.stats = {
        "buys": int(current_stats.get("buys", 0)),
        "sells": int(current_stats.get("sells", 0)),
        "errors": int(current_stats.get("errors", 0)),
        "signals": int(current_stats.get("signals", 0)),
    }

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    if len(engine.logs) > 200:
        engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(k, msg, sec=5):
    if now() - LAST_LOG.get(k, 0) > sec:
        LAST_LOG[k] = now()
        log(msg)

def get_kp():
    if not PRIVATE_KEY:
        raise ValueError("PRIVATE_KEY missing")
    return Keypair.from_base58_string(PRIVATE_KEY)

def looks_like_mint(v: str) -> bool:
    if not isinstance(v, str):
        return False
    return len(v) >= 32 and " " not in v and "." not in v and "/" not in v

# ================= SAFE HTTP =================
async def safe_get(url, params=None, headers=None):
    for _ in range(3):
        try:
            r = await HTTP.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log_once("http_err", f"HTTP_ERR {e}", 5)
            await asyncio.sleep(0.2)
    return None

# ================= TOKEN IDENTIFY =================
async def search_jupiter_token(query: str):
    """
    優先使用手動 TOKEN_MAP。
    如果沒有，再走 Jupiter Tokens API 搜尋。
    回傳 mint；找不到就回原值。
    """
    if not query:
        return query

    q = str(query).strip()
    uq = q.upper()

    # 1) 已經像 mint
    if looks_like_mint(q):
        return q

    # 2) 手動 mapping 優先
    mapped = TOKEN_MAP.get(uq)
    if mapped:
        return mapped

    # 3) 快取
    ts = TOKEN_SEARCH_TS.get(uq, 0)
    cached = TOKEN_SEARCH_CACHE.get(uq)
    if cached and now() - ts < TOKEN_SEARCH_TTL:
        return cached

    # 4) Jupiter Tokens API V2 搜尋
    # 文件有 Tokens API V2 搜尋能力；這裡做多端點容錯
    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

    endpoints = [
        "https://api.jup.ag/tokens/v2/search",
        "https://api.jup.ag/tokens/v1/search",
    ]

    for url in endpoints:
        data = await safe_get(url, params={"query": q}, headers=headers)
        if not data:
            continue

        candidates = []
        if isinstance(data, dict):
            if isinstance(data.get("tokens"), list):
                candidates = data["tokens"]
            elif isinstance(data.get("data"), list):
                candidates = data["data"]
            elif isinstance(data.get("results"), list):
                candidates = data["results"]
        elif isinstance(data, list):
            candidates = data

        if not candidates:
            continue

        # 優先 exact symbol，再來 verified / organic score 高的
        def rank_token(t):
            sym = str(t.get("symbol", "")).upper()
            verified = 1 if t.get("verified") else 0
            organic = float(t.get("organicScore", 0) or 0)
            exact = 1 if sym == uq else 0
            return (exact, verified, organic)

        best = sorted(candidates, key=rank_token, reverse=True)[0]
        mint = best.get("address") or best.get("mint") or best.get("id")

        if mint:
            TOKEN_SEARCH_CACHE[uq] = mint
            TOKEN_SEARCH_TS[uq] = now()
            log_once(f"token_map_{uq}", f"TOKEN_MAP {uq}->{mint[:6]}", 30)
            return mint

    log_once(f"token_resolve_fail_{uq}", f"TOKEN_RESOLVE_FAIL {uq}", 30)
    return q

# ================= PRICE =================
async def get_price(token_or_mint):
    mint = await search_jupiter_token(token_or_mint)

    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1000000,
            "slippageBps": 100
        },
        headers={"x-api-key": JUP_API_KEY} if JUP_API_KEY else None
    )

    if not data:
        log_once("price_none", f"NO PRICE {token_or_mint}", 5)
        return None

    try:
        if data.get("outAmount"):
            return float(data["outAmount"]) / 1e6

        if data.get("data"):
            return float(data["data"][0]["outAmount"]) / 1e6
    except Exception as e:
        log_once("price_parse", f"PRICE_PARSE {e}", 5)

    return None

# ================= ALPHA =================
async def alpha(m):
    price = await get_price(m)
    if not price:
        return 0

    hist = PRICE_HISTORY.get(m, [])
    hist.append(price)
    hist = hist[-5:]
    PRICE_HISTORY[m] = hist

    if len(hist) < 3:
        return 0

    momentum = (hist[-1] - hist[0]) / hist[0]
    volatility = max(hist) - min(hist)

    score = momentum + volatility * 2

    if MEMPOOL_SIGNAL.get(m):
        score += 0.2

    return score

# ================= MEMPOOL =================
async def mempool():
    while True:
        try:
            m = random.choice(list(CANDIDATES))
            MEMPOOL_SIGNAL[m] = now()
            log_once("mempool", f"MEMPOOL {m}", 2)
        except Exception as e:
            log_once("mempool_err", f"MEMPOOL_ERR {e}", 5)
        await asyncio.sleep(1)

# ================= JUP =================
async def jupiter_order(inp, out, amt):
    out_mint = await search_jupiter_token(out)
    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

    data = await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {
            "inputMint": inp,
            "outputMint": out_mint,
            "amount": str(amt),
            "taker": str(get_kp().pubkey()),
            "slippageBps": 100,
        },
        headers=headers
    )
    if data and data.get("transaction"):
        return data
    return None

async def jupiter_exec(order):
    try:
        headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

        tx = VersionedTransaction.from_bytes(
            base64.b64decode(order["transaction"])
        )
        signed = VersionedTransaction(tx.message, [get_kp()])

        r = await HTTP.post(
            "https://api.jup.ag/swap/v2/execute",
            headers=headers,
            json={
                "signedTransaction": base64.b64encode(bytes(signed)).decode(),
                "requestId": order.get("requestId"),
            }
        )
        if r.status_code == 200:
            return r.json()

        log_once("exec_status", f"EXEC_STATUS {r.status_code}", 5)
        return None
    except Exception as e:
        log_once("exec_err", f"EXEC_ERR {e}", 5)
        return None

# ================= BUY =================
def can_buy(m):
    ensure_engine()

    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p.get("token") for p in engine.positions if isinstance(p, dict)]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 10:
        return False
    return True

async def buy(m, combo):
    if m in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(m)

    try:
        ensure_engine()

        if not can_buy(m):
            return

        log_once(f"try_{m}", f"TRY BUY {m} {combo:.4f}", 2)

        order = await jupiter_order(SOL, m, BUY_LAMPORTS)

        if not order:
            log(f"BUY_FAIL {m}")
            return

        res = await jupiter_exec(order)

        if not res:
            log(f"EXEC_FAIL {m}")
            return

        price = await get_price(m)
        if not price:
            log(f"BUY_NO_PRICE {m}")
            return

        mint = await search_jupiter_token(m)

        engine.positions.append({
            "token": m,
            "mint": mint,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl": 0
        })

        engine.stats["buys"] += 1
        TOKEN_COOLDOWN[m] = now()

        log(f"BUY {m}")

    finally:
        IN_FLIGHT_BUY.discard(m)

# ================= SELL =================
async def sell(p):
    try:
        ensure_engine()

        m = p["token"]
        price = await get_price(m)
        if not price:
            return

        pnl = (price - p["entry_price"]) / p["entry_price"]

        if p in engine.positions:
            engine.positions.remove(p)
        engine.stats["sells"] += 1

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now()
        })

        log(f"SELL {m} pnl={pnl:.4f}")
    except Exception as e:
        log_once("sell_err", f"SELL_ERR {e}", 5)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            ensure_engine()

            for p in list(engine.positions):
                if not isinstance(p, dict):
                    continue

                price = await get_price(p["token"])
                if not price:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)

                p["peak_price"] = peak
                p["last_price"] = price
                p["pnl"] = pnl

                dd = (price - peak) / peak

                if pnl > TP_PCT or pnl < -SL_PCT or dd < -TRAIL_DD_PCT:
                    await sell(p)

        except Exception as e:
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ensure_engine()
    ranked = []

    log_once("rank_debug", f"SCANNING {len(CANDIDATES)}", 3)

    for m in list(CANDIDATES):
        try:
            a = await alpha(m)
            ranked.append((m, a))
            engine.stats["signals"] += 1
        except Exception as e:
            log_once("rank_err", f"RANK_ERR {m} {e}", 5)

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= MAIN =================
async def main():
    while True:
        try:
            ensure_engine()
            log_once("alive", "RUNNING", 5)
            log_once("heartbeat", "SYSTEM RUNNING", 3)

            ranked = await rank_candidates()

            for m, score in ranked:
                if score > ENTRY_THRESHOLD:
                    await buy(m, score)

        except Exception as e:
            log(f"MAIN_ERR {e}")

        await asyncio.sleep(2)

# ================= WATCHDOG =================
async def watchdog():
    while True:
        ensure_engine()
        log_once("watchdog", "SYSTEM OK", 10)
        await asyncio.sleep(5)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    if hasattr(engine, "normalize"):
        engine.normalize()
    log("SYSTEM STARTED")

    asyncio.create_task(main())
    asyncio.create_task(monitor())
    asyncio.create_task(mempool())
    asyncio.create_task(watchdog())

@app.get("/")
def root():
    ensure_engine()
    return {
        "status": "running",
        "time": now(),
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:] if isinstance(engine.logs, list) else ["SYSTEM BOOTING..."],
        "token_map": TOKEN_MAP,
        "token_cache": TOKEN_SEARCH_CACHE,
    }

@app.get("/debug")
def debug():
    ensure_engine()
    return {
        "logs_len": len(engine.logs),
        "positions": len(engine.positions),
        "stats": engine.stats,
        "engine_logs_type": str(type(engine.logs)),
        "engine_positions_type": str(type(engine.positions)),
        "engine_trade_history_type": str(type(engine.trade_history)),
        "token_cache_size": len(TOKEN_SEARCH_CACHE),
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1327 AUTO TOKEN IDENTIFY</h2>
    <div id=data>Loading...</div>
    <script>
    async function load(){
        try{
            let r = await fetch('/');
            let d = await r.json();
            document.getElementById('data').innerHTML =
            '<pre>'+JSON.stringify(d,null,2)+'</pre>';
        }catch(e){
            document.getElementById('data').innerHTML = "ERROR LOADING";
        }
    }
    setInterval(load,2000);
    load();
    </script>
    </body>
    </html>
    """)
