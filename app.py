# ================= v1324 REAL DATA SNIPER (KEEP ALL FEATURES) =================
import os
import time
import json
import base64
import random
import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from state import engine

# ================= CONFIG =================
SOL_MINT = "So11111111111111111111111111111111111111112"

JUP_API_KEY = os.getenv("JUP_API_KEY", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "2"))
ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))

TP_PCT = float(os.getenv("TP_PCT", "0.10"))
SL_PCT = float(os.getenv("SL_PCT", "0.05"))
TRAIL_DD_PCT = float(os.getenv("TRAIL_DD_PCT", "0.05"))

BUY_SIZE_LAMPORTS = int(os.getenv("BUY_SIZE_LAMPORTS", "1000000"))
DISCOVERY_LIMIT = int(os.getenv("DISCOVERY_LIMIT", "12"))
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "25000"))
MIN_VOL_5M_USD = float(os.getenv("MIN_VOL_5M_USD", "2000"))
MAX_TOKEN_AGE_MIN = int(os.getenv("MAX_TOKEN_AGE_MIN", "720"))  # 12 小時
DISCOVERY_REFRESH_SEC = int(os.getenv("DISCOVERY_REFRESH_SEC", "30"))

HTTP = httpx.AsyncClient(timeout=20)

# ================= STATIC TOKENS (KEEP ORIGINAL FEATURES) =================
# 保留你原本候選清單，外加真實發現的新幣
TOKEN_MINTS = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    # 下面幾個如果你手上有你自己確認過的 mint，直接改成你自己的
    "WIF": os.getenv("WIF_MINT", ""),
    "MYRO": os.getenv("MYRO_MINT", ""),
    "POPCAT": os.getenv("POPCAT_MINT", ""),
}

BASE_CANDIDATES = {"BONK", "WIF", "JUP", "MYRO", "POPCAT"}

# ================= GLOBAL =================
TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}
DISCOVERED_TOKENS: Dict[str, Dict[str, Any]] = {}
PAIR_CACHE: Dict[str, Dict[str, Any]] = {}
PRICE_CACHE: Dict[str, Dict[str, Any]] = {}

# ================= UTIL =================
def now() -> float:
    return time.time()

def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, (tuple, set)):
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
        "discovered": int(current_stats.get("discovered", 0)),
    }

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    if len(engine.logs) > 200:
        engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(key, msg, sec=5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

def get_keypair():
    if DRY_RUN:
        return None
    if not PRIVATE_KEY:
        raise ValueError("PRIVATE_KEY missing")
    return Keypair.from_base58_string(PRIVATE_KEY)

def symbol_to_mint(symbol: str) -> str:
    # 已發現的新幣直接回 mint
    if symbol in DISCOVERED_TOKENS and DISCOVERED_TOKENS[symbol].get("mint"):
        return DISCOVERED_TOKENS[symbol]["mint"]
    # 靜態映射
    if TOKEN_MINTS.get(symbol):
        return TOKEN_MINTS[symbol]
    # fallback: 如果本來傳入就是 mint，就原樣回傳
    return symbol

def mint_to_symbol(mint: str) -> str:
    for sym, mapped in TOKEN_MINTS.items():
        if mapped == mint:
            return sym
    for sym, meta in DISCOVERED_TOKENS.items():
        if meta.get("mint") == mint:
            return sym
    return mint[:6]

def safe_float(v, default=0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default

def safe_int(v, default=0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except Exception:
        return default

# ================= REAL DATA: DEXSCREENER =================
async def ds_get_json(url: str) -> Any:
    r = await HTTP.get(url, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()

async def fetch_latest_token_profiles() -> List[Dict[str, Any]]:
    data = await ds_get_json("https://api.dexscreener.com/token-profiles/latest/v1")
    if isinstance(data, list):
        return [x for x in data if x.get("chainId") == "solana"]
    return []

async def fetch_latest_token_boosts() -> List[Dict[str, Any]]:
    data = await ds_get_json("https://api.dexscreener.com/token-boosts/latest/v1")
    if isinstance(data, list):
        return [x for x in data if x.get("chainId") == "solana"]
    return []

async def fetch_top_token_boosts() -> List[Dict[str, Any]]:
    data = await ds_get_json("https://api.dexscreener.com/token-boosts/top/v1")
    if isinstance(data, list):
        return [x for x in data if x.get("chainId") == "solana"]
    return []

async def fetch_token_pairs(mint: str) -> List[Dict[str, Any]]:
    data = await ds_get_json(f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}")
    return data if isinstance(data, list) else []

async def fetch_tokens_bulk(mints: List[str]) -> List[Dict[str, Any]]:
    mints = [m for m in mints if m]
    if not mints:
        return []
    chunks = [mints[i:i+30] for i in range(0, len(mints), 30)]
    out: List[Dict[str, Any]] = []
    for chunk in chunks:
        joined = ",".join(chunk)
        data = await ds_get_json(f"https://api.dexscreener.com/tokens/v1/solana/{joined}")
        if isinstance(data, list):
            out.extend(data)
    return out

def pick_best_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None
    # 優先流動性高，再看 5m 量
    def score(p: Dict[str, Any]) -> float:
        liq = safe_float((p.get("liquidity") or {}).get("usd"), 0)
        vol5 = safe_float((p.get("volume") or {}).get("m5"), 0)
        buys5 = safe_float((((p.get("txns") or {}).get("m5") or {}).get("buys")), 0)
        sells5 = safe_float((((p.get("txns") or {}).get("m5") or {}).get("sells")), 0)
        return liq + vol5 * 2 + (buys5 - sells5) * 20
    return sorted(pairs, key=score, reverse=True)[0]

async def refresh_discovery():
    global DISCOVERED_TOKENS

    try:
        profiles_task = fetch_latest_token_profiles()
        boosts_task = fetch_latest_token_boosts()
        top_boosts_task = fetch_top_token_boosts()

        profiles, boosts, top_boosts = await asyncio.gather(
            profiles_task, boosts_task, top_boosts_task
        )

        raw_candidates: Dict[str, Dict[str, Any]] = {}

        for item in profiles + boosts + top_boosts:
            mint = item.get("tokenAddress")
            if not mint:
                continue
            raw_candidates[mint] = {
                "mint": mint,
                "symbol": item.get("symbol") or mint[:6],
                "source": "dexscreener",
                "boost_amount": safe_float(item.get("amount"), 0),
                "boost_total": safe_float(item.get("totalAmount"), 0),
            }

        mints = list(raw_candidates.keys())[:DISCOVERY_LIMIT]
        token_pairs = await fetch_tokens_bulk(mints)

        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for p in token_pairs:
            base = ((p.get("baseToken") or {}).get("address")) or ""
            quote = ((p.get("quoteToken") or {}).get("address")) or ""
            if base in raw_candidates:
                grouped[base].append(p)
            if quote in raw_candidates:
                grouped[quote].append(p)

        fresh: Dict[str, Dict[str, Any]] = {}
        now_ms = int(time.time() * 1000)

        for mint, meta in raw_candidates.items():
            best = pick_best_pair(grouped.get(mint, []))
            if not best:
                continue

            price_usd = safe_float(best.get("priceUsd"), 0)
            liquidity_usd = safe_float((best.get("liquidity") or {}).get("usd"), 0)
            vol_5m = safe_float((best.get("volume") or {}).get("m5"), 0)
            price_change_5m = safe_float((best.get("priceChange") or {}).get("m5"), 0)
            buys_5m = safe_int((((best.get("txns") or {}).get("m5") or {}).get("buys")), 0)
            sells_5m = safe_int((((best.get("txns") or {}).get("m5") or {}).get("sells")), 0)
            pair_created_at = safe_int(best.get("pairCreatedAt"), 0)

            age_min = 999999
            if pair_created_at > 0:
                age_min = max(0, int((now_ms - pair_created_at) / 60000))

            if liquidity_usd < MIN_LIQ_USD:
                continue
            if vol_5m < MIN_VOL_5M_USD:
                continue
            if age_min > MAX_TOKEN_AGE_MIN:
                continue

            symbol = ((best.get("baseToken") or {}).get("symbol")) or meta["symbol"] or mint[:6]
            symbol = symbol.upper()

            fresh[symbol] = {
                "mint": mint,
                "symbol": symbol,
                "price_usd": price_usd,
                "liquidity_usd": liquidity_usd,
                "vol_5m": vol_5m,
                "price_change_5m": price_change_5m,
                "buys_5m": buys_5m,
                "sells_5m": sells_5m,
                "age_min": age_min,
                "boost_total": meta["boost_total"],
                "pair_address": best.get("pairAddress"),
                "pair_url": best.get("url"),
                "dex_id": best.get("dexId"),
                "source": "discovery",
                "updated_at": now(),
            }

            PAIR_CACHE[mint] = best
            PRICE_CACHE[mint] = {
                "price_usd": price_usd,
                "updated_at": now(),
            }

        DISCOVERED_TOKENS = fresh
        engine.stats["discovered"] = len(DISCOVERED_TOKENS)
        log_once("discover", f"DISCOVERED {len(DISCOVERED_TOKENS)} TOKENS", 5)

    except Exception as e:
        engine.stats["errors"] += 1
        log_once("discover_err", f"DISCOVERY_ERR {type(e).__name__}: {e}", 5)

async def discovery_loop():
    while True:
        await refresh_discovery()
        await asyncio.sleep(DISCOVERY_REFRESH_SEC)

# ================= REAL PRICE =================
async def get_price(symbol_or_mint: str) -> float:
    mint = symbol_to_mint(symbol_or_mint)
    cache = PRICE_CACHE.get(mint)
    if cache and now() - cache.get("updated_at", 0) < 10:
        return safe_float(cache.get("price_usd"), 0)

    try:
        pairs = await fetch_token_pairs(mint)
        best = pick_best_pair(pairs)
        if best:
            px = safe_float(best.get("priceUsd"), 0)
            PRICE_CACHE[mint] = {"price_usd": px, "updated_at": now()}
            PAIR_CACHE[mint] = best
            return px
    except Exception:
        pass

    # fallback，避免整個流程炸掉
    return safe_float((DISCOVERED_TOKENS.get(symbol_or_mint) or {}).get("price_usd"), 0)

# ================= REAL ALPHA =================
async def alpha_momentum(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    return safe_float((pair.get("priceChange") or {}).get("m5"), 0) / 100.0

async def alpha_volume(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    vol_5m = safe_float((pair.get("volume") or {}).get("m5"), 0)
    # 壓成比較穩的分數
    return min(vol_5m / 100000.0, 0.10)

async def alpha_early(symbol: str) -> float:
    meta = DISCOVERED_TOKENS.get(symbol, {})
    age_min = safe_float(meta.get("age_min"), 999999)
    if age_min <= 15:
        return 0.08
    if age_min <= 60:
        return 0.05
    if age_min <= 180:
        return 0.02
    return 0.0

async def alpha_boost(symbol: str) -> float:
    meta = DISCOVERED_TOKENS.get(symbol, {})
    boost_total = safe_float(meta.get("boost_total"), 0)
    return min(boost_total / 10000.0, 0.08)

def alpha_wallet(symbol: str) -> float:
    # 先保留你原本功能接口，真 wallet tracking 之後可直接替換這裡
    return 1.0

async def compute_alpha(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}

    mom = await alpha_momentum(symbol)
    vol = await alpha_volume(symbol)
    early = await alpha_early(symbol)
    boost = await alpha_boost(symbol)
    wallet = alpha_wallet(symbol) * 0.01

    buys_5m = safe_float((((pair.get("txns") or {}).get("m5") or {}).get("buys")), 0)
    sells_5m = safe_float((((pair.get("txns") or {}).get("m5") or {}).get("sells")), 0)
    orderflow = min(max((buys_5m - sells_5m) / 200.0, -0.05), 0.08)

    liq = safe_float((pair.get("liquidity") or {}).get("usd"), 0)
    liq_bonus = min(liq / 500000.0, 0.05)

    score = mom * 1.2 + vol * 0.8 + early * 1.5 + boost * 1.2 + orderflow + liq_bonus + wallet
    return score

# ================= JUPITER V2 =================
async def jupiter_order(input_mint, output_mint, amount):
    log_once("jup_call", f"CALL JUP {input_mint[:4]}->{output_mint[:4]}", 2)

    if DRY_RUN:
        return {
            "transaction": "DRY_TX",
            "requestId": f"dry_{int(now())}",
            "inputMint": input_mint,
            "outputMint": output_mint,
            "inAmount": str(amount),
        }

    if not JUP_API_KEY:
        raise ValueError("JUP_API_KEY missing")

    headers = {"x-api-key": JUP_API_KEY}
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "swapMode": "ExactIn",
        "slippageBps": SLIPPAGE_BPS,
        "taker": str(get_keypair().pubkey()),
    }

    r = await HTTP.get(
        "https://api.jup.ag/swap/v2/order",
        params=params,
        headers=headers,
    )
    r.raise_for_status()
    data = r.json()

    if data.get("error") or data.get("errorCode") or data.get("errorMessage"):
        log_once("jup_order_err", f"ORDER_ERR {data}", 3)
        return None

    if not data.get("transaction"):
        log_once("jup_no_tx", f"NO TX {output_mint[:6]}", 5)
        return None

    return data

async def safe_jupiter_order(a, b, amt):
    for _ in range(3):
        try:
            d = await jupiter_order(a, b, amt)
            if d:
                return d
        except Exception as e:
            log_once("jup_err", f"JUP_ERR {type(e).__name__}: {e}", 5)
        await asyncio.sleep(0.4)
    return None

async def safe_jupiter_execute(order):
    if DRY_RUN:
        await asyncio.sleep(0.05)
        return {"signature": f"dry_tx_{int(time.time())}"}

    if not JUP_API_KEY:
        raise ValueError("JUP_API_KEY missing")

    tx_b64 = order["transaction"]
    raw = base64.b64decode(tx_b64)
    kp = get_keypair()
    tx = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(tx.message, [kp])

    headers = {"x-api-key": JUP_API_KEY}
    body = {
        "signedTransaction": base64.b64encode(bytes(signed)).decode(),
        "requestId": order.get("requestId"),
    }

    r = await HTTP.post(
        "https://api.jup.ag/swap/v2/execute",
        headers=headers,
        json=body,
    )
    r.raise_for_status()
    data = r.json()

    sig = data.get("signature") or data.get("txid")
    if not sig:
        log_once("exec_fail", f"EXEC_FAIL {data}", 3)
        return None

    return {"signature": sig, "raw": data}

# ================= BUY =================
def can_buy(symbol):
    ensure_engine()

    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if symbol in [p["token"] for p in engine.positions]:
        return False
    if now() - TOKEN_COOLDOWN[symbol] < 10:
        return False
    return True

async def buy(symbol, combo):
    ensure_engine()

    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if not can_buy(symbol):
            return

        log_once(f"try_{symbol}", f"TRY BUY {symbol} combo={combo:.4f}", 3)

        output_mint = symbol_to_mint(symbol)
        if not output_mint:
            log_once("buy_no_mint", f"BUY_NO_MINT {symbol}", 5)
            return

        order = await safe_jupiter_order(SOL_MINT, output_mint, BUY_SIZE_LAMPORTS)

        if not order:
            log_once("buy_fail", f"BUY_FAIL {symbol}", 5)
            return

        exec_res = await safe_jupiter_execute(order)
        if not exec_res:
            log_once("buy_exec_fail", f"BUY_EXEC_FAIL {symbol}", 5)
            return

        price = await get_price(symbol)
        if price <= 0:
            log_once("buy_no_price", f"BUY_NO_PRICE {symbol}", 5)
            return

        engine.positions.append({
            "token": symbol,
            "mint": output_mint,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "entry_ts": now(),
            "signature": exec_res["signature"],
            "combo": combo,
            "pnl_pct": 0.0
        })

        TOKEN_COOLDOWN[symbol] = now()
        engine.stats["buys"] += 1

        log(f"BUY SUCCESS {symbol}")

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"BUY ERR {symbol} {type(e).__name__}: {e}")

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= SELL =================
async def sell(p):
    ensure_engine()

    symbol = p["token"]
    if symbol in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(symbol)

    try:
        if not DRY_RUN:
            order = await safe_jupiter_order(
                p["mint"],
                SOL_MINT,
                BUY_SIZE_LAMPORTS
            )
            if not order:
                log(f"SELL_FAIL {symbol}")
                return

            exec_res = await safe_jupiter_execute(order)
            if not exec_res:
                log(f"SELL_EXEC_FAIL {symbol}")
                return

        price = await get_price(symbol)
        pnl = (price - p["entry_price"]) / p["entry_price"] if p["entry_price"] else 0.0

        if p in engine.positions:
            engine.positions.remove(p)

        engine.trade_history.append({
            "token": symbol,
            "pnl_pct": pnl,
            "ts": now()
        })

        engine.stats["sells"] += 1
        log(f"SELL {symbol} pnl={pnl:.4f}")

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"SELL_ERR {symbol} {type(e).__name__}: {e}")

    finally:
        IN_FLIGHT_SELL.discard(symbol)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])
                if price <= 0:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)

                p["peak_price"] = peak
                p["last_price"] = price
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak if peak else 0.0

                if pnl > TP_PCT or pnl < -SL_PCT or drawdown < -TRAIL_DD_PCT:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ranked = []

    # 保留你原本固定候選
    for symbol in list(BASE_CANDIDATES):
        mint = symbol_to_mint(symbol)
        if not mint:
            continue
        score = await compute_alpha(symbol)
        ranked.append((symbol, score))
        engine.stats["signals"] += 1

    # 加上真實發現的新幣
    for symbol in list(DISCOVERED_TOKENS.keys())[:DISCOVERY_LIMIT]:
        score = await compute_alpha(symbol)
        ranked.append((symbol, score))
        engine.stats["signals"] += 1

    ranked.sort(key=lambda x: x[1], reverse=True)

    # 去重，保留分數最高那筆
    seen = set()
    uniq = []
    for symbol, score in ranked:
        if symbol in seen:
            continue
        seen.add(symbol)
        uniq.append((symbol, score))

    return uniq[:10]

# ================= MAIN =================
async def main_loop():
    while True:
        try:
            ranked = await rank_candidates()

            log_once("rank", f"RANKED {len(ranked)}", 5)

            for symbol, combo in ranked:
                if combo > ENTRY_THRESHOLD:
                    await buy(symbol, combo)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(3)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()

    engine.positions = []
    engine.trade_history = []
    engine.logs = []
    engine.stats = {"buys": 0, "sells": 0, "errors": 0, "signals": 0, "discovered": 0}

    asyncio.create_task(discovery_loop())
    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())

@app.on_event("shutdown")
async def shutdown():
    await HTTP.aclose()

@app.get("/")
def root():
    ensure_engine()
    return {
        "mode": "DRY_RUN" if DRY_RUN else "REAL",
        "positions": engine.positions,
        "stats": engine.stats,
        "discovered": list(DISCOVERED_TOKENS.values())[:10],
        "logs": engine.logs[-20:]
    }

@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": "DRY_RUN" if DRY_RUN else "REAL",
        "base_candidates": list(BASE_CANDIDATES),
        "discovered_count": len(DISCOVERED_TOKENS),
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime;font-family:monospace">
    <h2>🔥 v1324 REAL DATA SNIPER</h2>
    <div id="data"></div>
    <script>
    async function load(){
        let res = await fetch('/');
        let d = await res.json();
        document.getElementById("data").innerHTML =
            "<pre>"+JSON.stringify(d,null,2)+"</pre>";
    }
    setInterval(load, 2000);
    load();
    </script>
    </body>
    </html>
    """)
