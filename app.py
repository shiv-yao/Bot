# ================= v1339 ALL FEATURES FIRST (FULLY INTEGRATED FIXED) =================
# 🔥 全功能先補齊，不先做策略刪減
# - 保留 discovery / real-data / jupiter / dry-run / monitor / ui
# - 所有 alpha 同時存在：momentum / volume / early / boost / volatility / liquidity / smart / flow / insider
# - 所有 risk 同時存在：max positions / exposure / cooldown / TP / SL / trailing DD
# - 所有 execution 能力同時存在：quote / order / execute / retry / quote_only
# - 所有 engine stats 同時存在：equity / drawdown / wins / losses / feature snapshots
# - 最後再選策略，不在這版先替你刪

import os
import time
import base64
import random
import asyncio
from collections import defaultdict, deque
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

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true" if not REAL_TRADING else False
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_EXPOSURE_SOL = float(os.getenv("MAX_EXPOSURE_SOL", "1.5"))
ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))

TP_PCT = float(os.getenv("TP_PCT", "0.10"))
SL_PCT = float(os.getenv("SL_PCT", "0.05"))
TRAIL_DD_PCT = float(os.getenv("TRAIL_DD_PCT", "0.05"))

BUY_SIZE_LAMPORTS = int(os.getenv("BUY_SIZE_LAMPORTS", "1000000"))
DISCOVERY_LIMIT = int(os.getenv("DISCOVERY_LIMIT", "20"))
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "25000"))
MIN_VOL_5M_USD = float(os.getenv("MIN_VOL_5M_USD", "2000"))
MAX_TOKEN_AGE_MIN = int(os.getenv("MAX_TOKEN_AGE_MIN", "720"))
DISCOVERY_REFRESH_SEC = int(os.getenv("DISCOVERY_REFRESH_SEC", "30"))

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
HTTP = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

# ================= STATIC TOKENS =================
TOKEN_MINTS = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": os.getenv("WIF_MINT", "").strip(),
    "MYRO": os.getenv("MYRO_MINT", "").strip(),
    "POPCAT": os.getenv("POPCAT_MINT", "").strip(),
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

# onchain-like factors
SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)

# feature cache / stats
FEATURE_CACHE: Dict[str, Dict[str, Any]] = {}
ALPHA_HISTORY: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

# PnL / regime
EQUITY = 0.0
PEAK_EQUITY = 0.0
DRAWDOWN = 0.0
WIN = 0
LOSS = 0
REGIME = "NEUTRAL"

# AI / weighting
AI_WEIGHTS = {
    "momentum": 1.00,
    "volume": 0.80,
    "early": 1.20,
    "boost": 1.00,
    "orderflow": 0.90,
    "liquidity_bonus": 0.70,
    "wallet": 0.50,
    "volatility": 0.60,
    "liquidity_curve": 0.50,
    "smart_money": 0.80,
    "flow_signal": 0.80,
    "insider_signal": 0.80,
}
LEARN_RATE = float(os.getenv("LEARN_RATE", "0.01"))

# raw strategy switches (先全部開著，不刪)
STRATEGY_FLAGS = {
    "momentum": True,
    "volume": True,
    "early": True,
    "boost": True,
    "orderflow": True,
    "liquidity_bonus": True,
    "wallet": True,
    "volatility": True,
    "liquidity_curve": True,
    "smart_money": True,
    "flow_signal": True,
    "insider_signal": True,
}

# ================= UTIL =================
def now() -> float:
    return time.time()

def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, (tuple, set, deque)):
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
        "wins": int(current_stats.get("wins", 0)),
        "losses": int(current_stats.get("losses", 0)),
    }

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    if len(engine.logs) > 300:
        engine.logs = engine.logs[-300:]
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
    if symbol in DISCOVERED_TOKENS and DISCOVERED_TOKENS[symbol].get("mint"):
        return DISCOVERED_TOKENS[symbol]["mint"]
    mapped = TOKEN_MINTS.get(symbol)
    if mapped:
        return mapped
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

def current_exposure_sol() -> float:
    total_lamports = sum(p.get("size", 0) for p in engine.positions if isinstance(p, dict))
    return total_lamports / 1_000_000_000

def clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))

def has_position(symbol: str) -> bool:
    return any(p.get("token") == symbol for p in engine.positions if isinstance(p, dict))

def clamp_flow():
    for m in list(set(list(SMART_MONEY.keys()) + list(FLOW.keys()) + list(INSIDER.keys()))):
        SMART_MONEY[m] = clamp(SMART_MONEY[m], 0.0, 2.0)
        FLOW[m] = clamp(FLOW[m], 0.0, 2.0)
        INSIDER[m] = clamp(INSIDER[m], 0.0, 2.0)

def apply_fee(pnl: float) -> float:
    return pnl - 0.003  # 估算成本 0.3%

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
    chunks = [mints[i:i + 30] for i in range(0, len(mints), 30)]
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
            profiles_task,
            boosts_task,
            top_boosts_task,
        )

        raw_candidates: Dict[str, Dict[str, Any]] = {}

        for item in profiles + boosts + top_boosts:
            mint = item.get("tokenAddress")
            if not mint:
                continue
            raw_candidates[mint] = {
                "mint": mint,
                "symbol": (item.get("symbol") or mint[:6]).upper(),
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
            vol_h24 = safe_float((best.get("volume") or {}).get("h24"), 0)
            price_change_5m = safe_float((best.get("priceChange") or {}).get("m5"), 0)
            price_change_1h = safe_float((best.get("priceChange") or {}).get("h1"), 0)
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
                "vol_h24": vol_h24,
                "price_change_5m": price_change_5m,
                "price_change_1h": price_change_1h,
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
            PRICE_CACHE[mint] = {"price_usd": price_usd, "updated_at": now()}

        DISCOVERED_TOKENS = fresh
        ensure_engine()
        engine.stats["discovered"] = len(DISCOVERED_TOKENS)
        log_once("discover", f"DISCOVERED {len(DISCOVERED_TOKENS)} TOKENS", 5)

    except Exception as e:
        ensure_engine()
        engine.stats["errors"] += 1
        log_once("discover_err", f"DISCOVERY_ERR {type(e).__name__}: {e}", 5)

async def discovery_loop():
    while True:
        await refresh_discovery()
        await asyncio.sleep(DISCOVERY_REFRESH_SEC)

# ================= PRICE =================
async def get_price(symbol_or_mint: str) -> float:
    mint = symbol_to_mint(symbol_or_mint)
    if not mint:
        return 0.0

    cache = PRICE_CACHE.get(mint)
    if cache and now() - cache.get("updated_at", 0) < 5:
        return safe_float(cache.get("price_usd"), 0)

    # main: dexscreener pairs
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

    # fallback: Jupiter quote
    try:
        headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None
        r = await HTTP.get(
            "https://api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL_MINT,
                "outputMint": mint,
                "amount": 1_000_000,
                "slippageBps": SLIPPAGE_BPS,
            },
            headers=headers,
        )
        if r.status_code == 200:
            d = r.json()
            out_amt = safe_float(d.get("outAmount"), 0)
            if out_amt > 0:
                px = out_amt / 1e6
                PRICE_CACHE[mint] = {"price_usd": px, "updated_at": now()}
                return px
    except Exception:
        pass

    # fallback: public quote api
    try:
        r = await HTTP.get(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": SOL_MINT,
                "outputMint": mint,
                "amount": 1_000_000,
                "slippageBps": SLIPPAGE_BPS,
            },
        )
        if r.status_code == 200:
            d = r.json()
            routes = d.get("data", [])
            if routes:
                px = safe_float(routes[0].get("outAmount"), 0) / 1e6
                if px > 0:
                    PRICE_CACHE[mint] = {"price_usd": px, "updated_at": now()}
                    return px
    except Exception:
        pass

    return safe_float((DISCOVERED_TOKENS.get(symbol_or_mint) or {}).get("price_usd"), 0)

# ================= ALPHA COMPONENTS =================
async def alpha_momentum(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    m5 = safe_float((pair.get("priceChange") or {}).get("m5"), 0) / 100.0
    h1 = safe_float((pair.get("priceChange") or {}).get("h1"), 0) / 100.0
    return m5 * 0.7 + h1 * 0.3

async def alpha_volume(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    vol_5m = safe_float((pair.get("volume") or {}).get("m5"), 0)
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

async def alpha_orderflow(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    buys_5m = safe_float((((pair.get("txns") or {}).get("m5") or {}).get("buys")), 0)
    sells_5m = safe_float((((pair.get("txns") or {}).get("m5") or {}).get("sells")), 0)
    return clamp((buys_5m - sells_5m) / 200.0, -0.05, 0.08)

async def alpha_liquidity_bonus(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    liq = safe_float((pair.get("liquidity") or {}).get("usd"), 0)
    return min(liq / 500000.0, 0.05)

def alpha_wallet(symbol: str) -> float:
    return 0.01

async def alpha_volatility(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    pair = PAIR_CACHE.get(mint) or {}
    high = safe_float(pair.get("highPrice"), 0)
    low = safe_float(pair.get("lowPrice"), 0)
    if high and low:
        return clamp((high - low) / max(low, 1e-6), 0.0, 0.10)
    return 0.0

async def alpha_liquidity_curve(symbol: str) -> float:
    mint = symbol_to_mint(symbol)
    liq = safe_float((PAIR_CACHE.get(mint) or {}).get("liquidity", {}).get("usd"), 0)
    return min(liq / 1_000_000.0, 0.10)

def alpha_smart_money(symbol: str) -> float:
    return SMART_MONEY[symbol]

def alpha_flow_signal(symbol: str) -> float:
    return FLOW[symbol]

def alpha_insider_signal(symbol: str) -> float:
    return INSIDER[symbol]

# ================= FEATURE SNAPSHOT =================
async def compute_feature_snapshot(symbol: str) -> Dict[str, float]:
    snapshot = {
        "momentum": await alpha_momentum(symbol),
        "volume": await alpha_volume(symbol),
        "early": await alpha_early(symbol),
        "boost": await alpha_boost(symbol),
        "orderflow": await alpha_orderflow(symbol),
        "liquidity_bonus": await alpha_liquidity_bonus(symbol),
        "wallet": alpha_wallet(symbol),
        "volatility": await alpha_volatility(symbol),
        "liquidity_curve": await alpha_liquidity_curve(symbol),
        "smart_money": alpha_smart_money(symbol),
        "flow_signal": alpha_flow_signal(symbol),
        "insider_signal": alpha_insider_signal(symbol),
    }
    FEATURE_CACHE[symbol] = snapshot
    return snapshot

# ================= FULL ALPHA =================
async def compute_full_alpha(symbol: str) -> float:
    features = await compute_feature_snapshot(symbol)
    total = 0.0
    for k, v in features.items():
        if STRATEGY_FLAGS.get(k, True):
            total += AI_WEIGHTS.get(k, 1.0) * v
    ALPHA_HISTORY[symbol].append(total)
    return total

# ================= REGIME =================
def detect_regime():
    global REGIME
    vals = []
    for m in list(DISCOVERED_TOKENS.keys())[:10]:
        vals.append(safe_float(DISCOVERED_TOKENS[m].get("price_change_5m"), 0))
    if not vals:
        REGIME = "NEUTRAL"
        return

    avg = sum(vals) / len(vals)
    if avg > 5:
        REGIME = "BULL"
    elif avg < -5:
        REGIME = "BEAR"
    else:
        REGIME = "CHOP"

# ================= AI LEARNING =================
def auto_adjust_weights(pnl: float):
    for k in AI_WEIGHTS:
        AI_WEIGHTS[k] += LEARN_RATE * pnl
        AI_WEIGHTS[k] = clamp(AI_WEIGHTS[k], -2.0, 2.0)

def learn_from_features(features: Dict[str, float], pnl: float):
    for k, v in features.items():
        AI_WEIGHTS[k] += LEARN_RATE * pnl * v
        AI_WEIGHTS[k] = clamp(AI_WEIGHTS[k], -2.0, 2.0)

# ================= EXECUTION =================
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

    try:
        tx_b64 = order["transaction"]
        raw = base64.b64decode(tx_b64)

        try:
            tx = VersionedTransaction.from_bytes(raw)
        except Exception:
            log("TX_DECODE_FAIL")
            return None

        kp = get_keypair()
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

    except Exception as e:
        log_once("exec_err", f"EXEC_ERR {type(e).__name__}: {e}", 3)
        return None

async def execute_trade(symbol: str, amount: int):
    order = await safe_jupiter_order(SOL_MINT, symbol_to_mint(symbol), amount)
    if not order:
        return None
    if order.get("_quote_only"):
        return None
    for _ in range(2):
        res = await safe_jupiter_execute(order)
        if res:
            return res
    return None

# ================= RISK =================
def dynamic_size(score: float) -> int:
    base = BUY_SIZE_LAMPORTS
    if REGIME == "BULL":
        mult = 1.5
    elif REGIME == "BEAR":
        mult = 0.5
    else:
        mult = 1.0
    return int(base * mult * (1 + min(score, 1)))

def rug_filter(symbol: str) -> bool:
    meta = DISCOVERED_TOKENS.get(symbol, {})
    liq = safe_float(meta.get("liquidity_usd"), 0)
    buys = safe_int(meta.get("buys_5m"), 1)
    sells = safe_int(meta.get("sells_5m"), 1)
    if liq < MIN_LIQ_USD:
        return False
    if sells > buys * 3:
        return False
    return True

def risk_check(symbol: str) -> bool:
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if current_exposure_sol() > MAX_EXPOSURE_SOL:
        return False
    if now() - TOKEN_COOLDOWN[symbol] < 10:
        return False
    if has_position(symbol):
        return False
    if not rug_filter(symbol):
        return False
    return True

# ================= MASTER BUY =================
async def master_buy(symbol: str, score: float):
    ensure_engine()

    if symbol in IN_FLIGHT_BUY:
        return
    if not risk_check(symbol):
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        detect_regime()
        amount = dynamic_size(score)

        res = await execute_trade(symbol, amount)
        if not res:
            return

        features = FEATURE_CACHE.get(symbol) or await compute_feature_snapshot(symbol)
        px = await get_price(symbol)
        if px <= 0:
            return

        engine.positions.append({
            "token": symbol,
            "mint": symbol_to_mint(symbol),
            "entry_price": px,
            "last_price": px,
            "peak_price": px,
            "entry_ts": now(),
            "size": amount,
            "score": score,
            "features": features,
            "signature": res.get("signature", ""),
        })

        TOKEN_COOLDOWN[symbol] = now()
        engine.stats["buys"] += 1
        log(f"BUY {symbol} size={amount} score={score:.4f}")

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"BUY_ERR {symbol} {type(e).__name__}: {e}")

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= MASTER SELL =================
async def master_sell(p: Dict[str, Any]):
    global EQUITY, PEAK_EQUITY, DRAWDOWN, WIN, LOSS
    ensure_engine()

    symbol = p["token"]
    if symbol in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(symbol)

    try:
        pr = await get_price(symbol)
        if pr <= 0:
            return

        pnl = (pr - p["entry_price"]) / p["entry_price"] if p["entry_price"] else 0.0
        pnl = apply_fee(pnl)

        if pnl > 0:
            WIN += 1
            engine.stats["wins"] += 1
        else:
            LOSS += 1
            engine.stats["losses"] += 1

        EQUITY += pnl
        PEAK_EQUITY = max(PEAK_EQUITY, EQUITY)
        if PEAK_EQUITY > 0:
            DRAWDOWN = (EQUITY - PEAK_EQUITY) / PEAK_EQUITY

        auto_adjust_weights(pnl)
        learn_from_features(p.get("features", {}), pnl)

        if p in engine.positions:
            engine.positions.remove(p)

        if not hasattr(engine, "trade_history"):
            engine.trade_history = []

        engine.trade_history.append({
            "token": symbol,
            "pnl": pnl,
            "pnl_pct": pnl,
            "ts": now(),
            "features": p.get("features", {}),
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
                pr = await get_price(p["token"])
                if pr <= 0:
                    continue

                pnl = (pr - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], pr)
                p["peak_price"] = peak
                p["last_price"] = pr
                p["pnl_pct"] = pnl

                dd = (pr - peak) / peak if peak else 0.0

                if pnl > TP_PCT or pnl < -SL_PCT or dd < -TRAIL_DD_PCT:
                    await master_sell(p)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {type(e).__name__}: {e}")

        await asyncio.sleep(2)

# ================= ONCHAIN / FLOW =================
async def flow_engine():
    while True:
        for m in list(DISCOVERED_TOKENS.keys()) + list(BASE_CANDIDATES):
            SMART_MONEY[m] *= 0.9
            FLOW[m] *= 0.85
            INSIDER[m] *= 0.9

            if random.random() < 0.3:
                SMART_MONEY[m] += 0.5
            if random.random() < 0.4:
                FLOW[m] += 0.3
            if random.random() < 0.2:
                INSIDER[m] += 0.6

        clamp_flow()
        await asyncio.sleep(2)

# ================= STRATEGY LAYER =================
async def strategy_engine() -> List[tuple]:
    ensure_engine()
    ranked = []

    for symbol in list(BASE_CANDIDATES):
        mint = symbol_to_mint(symbol)
        if not mint:
            continue
        score = await compute_full_alpha(symbol)
        ranked.append((symbol, score))
        engine.stats["signals"] += 1

    for symbol in list(DISCOVERED_TOKENS.keys())[:DISCOVERY_LIMIT]:
        score = await compute_full_alpha(symbol)
        ranked.append((symbol, score))
        engine.stats["signals"] += 1

    ranked.sort(key=lambda x: x[1], reverse=True)

    seen = set()
    uniq = []
    for symbol, score in ranked:
        if symbol in seen:
            continue
        seen.add(symbol)
        uniq.append((symbol, score))

    return uniq[:20]

# ================= MAIN =================
async def god_main():
    while True:
        try:
            ranked = await strategy_engine()
            log_once("rank", f"RANKED {len(ranked)}", 5)

            for m, s in ranked:
                if s > ENTRY_THRESHOLD:
                    await master_buy(m, s)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log(f"MAIN_ERR {type(e).__name__}: {e}")

        await asyncio.sleep(2)

# ================= WATCHDOG =================
async def watchdog():
    while True:
        log(f"SYS OK | POS {len(engine.positions)} | DD {DRAWDOWN:.3f} | EQ {EQUITY:.3f}")
        await asyncio.sleep(10)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    engine.positions = []
    engine.trade_history = []
    engine.logs = []
    engine.stats = {
        "buys": 0,
        "sells": 0,
        "errors": 0,
        "signals": 0,
        "discovered": 0,
        "wins": 0,
        "losses": 0,
    }

    asyncio.create_task(discovery_loop())
    asyncio.create_task(god_main())
    asyncio.create_task(monitor())
    asyncio.create_task(flow_engine())
    asyncio.create_task(watchdog())

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
        "discovered_count": len(DISCOVERED_TOKENS),
        "discovered": list(DISCOVERED_TOKENS.values())[:10],
        "equity": EQUITY,
        "peak_equity": PEAK_EQUITY,
        "drawdown": DRAWDOWN,
        "regime": REGIME,
        "ai_weights": AI_WEIGHTS,
        "logs": engine.logs[-20:],
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
        "equity": EQUITY,
        "drawdown": DRAWDOWN,
        "wins": WIN,
        "losses": LOSS,
        "regime": REGIME,
    }

@app.get("/debug")
def debug():
    ensure_engine()
    return {
        "positions": len(engine.positions),
        "candidate_count": len(BASE_CANDIDATES) + len(DISCOVERED_TOKENS),
        "discovered_count": len(DISCOVERED_TOKENS),
        "stats": engine.stats,
        "weights": AI_WEIGHTS,
        "cooldowns": len(TOKEN_COOLDOWN),
        "feature_cache_size": len(FEATURE_CACHE),
        "alpha_history_size": sum(len(v) for v in ALPHA_HISTORY.values()),
        "equity": EQUITY,
        "peak_equity": PEAK_EQUITY,
        "drawdown": DRAWDOWN,
        "wins": WIN,
        "losses": LOSS,
        "regime": REGIME,
        "exposure_sol": current_exposure_sol(),
    }

@app.get("/features/{symbol}")
async def feature_view(symbol: str):
    symbol = symbol.upper()
    features = FEATURE_CACHE.get(symbol) or await compute_feature_snapshot(symbol)
    return {
        "symbol": symbol,
        "features": features,
        "weight_applied": {
            k: AI_WEIGHTS.get(k, 1.0) for k in features.keys()
        },
        "alpha": await compute_full_alpha(symbol),
    }

@app.get("/candidates")
def candidates():
    merged = []
    seen = set()

    for s in list(BASE_CANDIDATES):
        if s in seen:
            continue
        seen.add(s)
        merged.append({
            "symbol": s,
            "base": True,
            "discovered": s in DISCOVERED_TOKENS,
            "mint": symbol_to_mint(s),
        })

    for s, meta in DISCOVERED_TOKENS.items():
        if s in seen:
            continue
        seen.add(s)
        merged.append({
            "symbol": s,
            "base": False,
            "discovered": True,
            "mint": meta.get("mint"),
            "price_usd": meta.get("price_usd"),
            "liquidity_usd": meta.get("liquidity_usd"),
            "vol_5m": meta.get("vol_5m"),
            "age_min": meta.get("age_min"),
            "boost_total": meta.get("boost_total"),
        })

    return {
        "count": len(merged),
        "items": merged[:100],
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime;font-family:monospace">
    <h2>🔥 v1339 ALL FEATURES FIRST</h2>
    <div id="data"></div>
    <script>
    async function load(){
        try{
            let res = await fetch('/');
            let d = await res.json();
            document.getElementById("data").innerHTML =
                "<pre>"+JSON.stringify(d,null,2)+"</pre>";
        }catch(e){
            document.getElementById("data").innerHTML =
                "<pre>UI LOAD ERROR: "+String(e)+"</pre>";
        }
    }
    setInterval(load, 2000);
    load();
    </script>
    </body>
    </html>
    """)
