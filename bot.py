# ================= v1314 REAL TRADING HARDENED FINAL (JUP V2 ORDER + EXECUTE) =================
import os
import json
import time
import base64
import random
import asyncio
from collections import defaultdict

import httpx
import base58

from state import engine
from mempool import mempool_stream
from wallet_tracker import extract_wallets_from_mints, track_wallet_behavior

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders import message as solders_message

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB263kzwc"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

STATIC_UNIVERSE = {SOL, USDC, USDT, BONK, JUP}
FALLBACK_TOKENS = set(STATIC_UNIVERSE)

MAX_POSITION_SOL = float(os.environ.get("MAX_POSITION_SOL", "0.0025"))
MIN_POSITION_SOL = float(os.environ.get("MIN_POSITION_SOL", "0.001"))
MAX_POSITIONS = int(os.environ.get("MAX_POSITIONS", "5"))

PUMP_API = os.environ.get("PUMP_API", "https://frontend-api.pump.fun/coins/latest")
# ❗改成這樣（只留一個）
JUP_TOKENS_API = os.environ.get(
    "JUP_TOKENS_API",
    "https://lite-api.jup.ag/tokens"
)
JUP_BASE_API = os.environ.get("JUP_BASE_API", "https://api.jup.ag/swap/v2")
JUP_ORDER_API = f"{JUP_BASE_API}/order"
JUP_EXECUTE_API = f"{JUP_BASE_API}/execute"

RPC_HTTPS = [
    x.strip()
    for x in os.environ.get(
        "SOLANA_RPC_HTTPS",
        os.environ.get("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com"),
    ).split(",")
    if x.strip()
]

RPC_WSS = [
    x.strip()
    for x in os.environ.get(
        "SOLANA_RPC_WSS",
        os.environ.get("SOLANA_RPC_WS", "wss://api.mainnet-beta.solana.com"),
    ).split(",")
    if x.strip()
]

JITO_BUNDLE_URL = os.environ.get("JITO_BUNDLE_URL", "").strip()
USE_JITO = os.environ.get("USE_JITO", "false").lower() == "true"

REAL_TRADING = os.environ.get("REAL_TRADING", "false").lower() == "true"
JUP_API_KEY = os.environ.get("JUP_API_KEY", "").strip()

PRIVATE_KEY_JSON = os.environ.get("PRIVATE_KEY_JSON", "").strip()
PRIVATE_KEY_B58 = os.environ.get("PRIVATE_KEY_B58", "").strip()

RPC_MAX_CONCURRENCY = int(os.environ.get("RPC_MAX_CONCURRENCY", "6"))
RPC_TIMEOUT = float(os.environ.get("RPC_TIMEOUT", "12"))
RPC_RETRY = int(os.environ.get("RPC_RETRY", "4"))
WS_RECV_TIMEOUT = int(os.environ.get("WS_RECV_TIMEOUT", "45"))
WS_BACKOFF_MIN = int(os.environ.get("WS_BACKOFF_MIN", "2"))
WS_BACKOFF_MAX = int(os.environ.get("WS_BACKOFF_MAX", "20"))

EARLY_LIQ_MIN_OUT = int(os.environ.get("EARLY_LIQ_MIN_OUT", "1"))
EARLY_LIQ_MAX_PRICE_IMPACT = float(os.environ.get("EARLY_LIQ_MAX_PRICE_IMPACT", "0.85"))
STRICT_LIQ_MAX_PRICE_IMPACT = float(os.environ.get("STRICT_LIQ_MAX_PRICE_IMPACT", "0.55"))

FORCE_EARLY_ENTRY = os.environ.get("FORCE_EARLY_ENTRY", "true").lower() == "true"
EARLY_ENTRY_BONUS = float(os.environ.get("EARLY_ENTRY_BONUS", "0.012"))
SNIPER_RECENT_WINDOW_SEC = int(os.environ.get("SNIPER_RECENT_WINDOW_SEC", "90"))
EARLY_SOURCE_WINDOW_SEC = int(os.environ.get("EARLY_SOURCE_WINDOW_SEC", "120"))
FAKE_SIGNAL_MAX_AGE_SEC = int(os.environ.get("FAKE_SIGNAL_MAX_AGE_SEC", "240"))

MIN_STRICT_LIQ_OUT = int(os.environ.get("MIN_STRICT_LIQ_OUT", "50"))
MIN_SELLBACK_OUT = int(os.environ.get("MIN_SELLBACK_OUT", "1"))
ENABLE_MEMPOOL_LOGS = os.environ.get("ENABLE_MEMPOOL_LOGS", "true").lower() == "true"
ENABLE_MEMPOOL_STREAM = os.environ.get("ENABLE_MEMPOOL_STREAM", "true").lower() == "true"

HTTP = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

# ================= RPC POOL STATE =================
RPC_ROLE_INDEX = {
    "default": 0,
    "confirm": 0,
    "send": 0,
    "ws": 0,
}
RPC_BAD_UNTIL = {}
RPC_SEM = asyncio.Semaphore(RPC_MAX_CONCURRENCY)

# ================= WALLET =================
def load_keypair():
    last_err = None

    if PRIVATE_KEY_JSON:
        try:
            return Keypair.from_bytes(bytes(json.loads(PRIVATE_KEY_JSON)))
        except Exception as e:
            last_err = f"PRIVATE_KEY_JSON invalid: {e}"

    if PRIVATE_KEY_B58:
        try:
            return Keypair.from_bytes(base58.b58decode(PRIVATE_KEY_B58))
        except Exception as e:
            last_err = f"PRIVATE_KEY_B58 invalid: {e}"

    if last_err:
        raise RuntimeError(last_err)

    raise RuntimeError("No private key provided")


try:
    KEYPAIR = load_keypair()
except Exception:
    KEYPAIR = None

# ================= AI =================
AI_PARAMS = {
    "entry_threshold": 0.002,
    "size_multiplier": 1.0,
    "trailing_stop": 0.08,
    "slippage_bps": 80,
    "priority_fee_lamports": 5000,
    "jito_tip_lamports": 0,
}

# ================= STATE =================
IN_FLIGHT = set()
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
PRICE_CACHE = {}
LAST_LOG_TS = {}
LAST_WALLET_GRAPH_TS = 0.0
LAST_STRATEGY_REVIEW_TS = 0.0
TOKEN_DECIMALS = {}

# ================= WALLET GRAPH =================
WALLET_GRAPH = {}
WALLET_SCORE = {}

# ================= STRATEGY CONTROL =================
STRATEGY_ENABLED = {
    "stable": True,
    "degen": True,
    "sniper": True,
}

STRATEGY_LOCAL_STATS = {
    "stable": {"trades": 0, "wins": 0, "pnl": 0.0},
    "degen": {"trades": 0, "wins": 0, "pnl": 0.0},
    "sniper": {"trades": 0, "wins": 0, "pnl": 0.0},
}

# ================= MEMPOOL / EARLY LIQ STATE =================
SNIPER_CACHE = {}
RECENT_MEMPOOL_MINTS = {}
EARLY_LIQ_CACHE = {}
CANDIDATE_META = {}
WATCH_PROGRAMS = set(filter(None, [
    os.environ.get("WATCH_PROGRAM_1", ""),
    os.environ.get("WATCH_PROGRAM_2", ""),
]))

# ================= UTIL =================
def now() -> float:
    return time.time()


def rpc_now() -> float:
    return time.time()


def valid_mint(m) -> bool:
    return isinstance(m, str) and 32 <= len(m) <= 44


def ensure_float(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def ensure_int(x, d=0):
    try:
        return int(x)
    except Exception:
        return d


def real_trading_ready() -> bool:
    return REAL_TRADING and KEYPAIR is not None and bool(JUP_API_KEY)


def wallet_pubkey_str() -> str:
    return str(KEYPAIR.pubkey()) if KEYPAIR else ""


def log(msg: str):
    repair()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-500:]
    print("[BOT]", msg)


def log_once(key: str, msg: str, cooldown: int = 60):
    ts = now()
    if ts - LAST_LOG_TS.get(key, 0) > cooldown:
        LAST_LOG_TS[key] = ts
        log(msg)


def repair():
    if not hasattr(engine, "positions") or not isinstance(engine.positions, list):
        engine.positions = []
    if not hasattr(engine, "trade_history") or not isinstance(engine.trade_history, list):
        engine.trade_history = []
    if not hasattr(engine, "logs"):
        engine.logs = []
    if not isinstance(engine.logs, list):
        try:
            engine.logs = list(engine.logs)
        except Exception:
            engine.logs = []
    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {"signals": 0, "buys": 0, "sells": 0, "errors": 0, "adds": 0}
    if not hasattr(engine, "engine_stats") or not isinstance(engine.engine_stats, dict):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }
    if not hasattr(engine, "engine_allocator") or not isinstance(engine.engine_allocator, dict):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }
    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""
    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""
    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0
    if not hasattr(engine, "capital"):
        engine.capital = 30.0
    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 30.0
    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True
    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""


def pick_rpc_http(role="default"):
    if not RPC_HTTPS:
        raise RuntimeError("NO_RPC_HTTP_CONFIG")

    start = RPC_ROLE_INDEX.get(role, 0)
    n = len(RPC_HTTPS)

    for i in range(n):
        idx = (start + i) % n
        url = RPC_HTTPS[idx]
        if rpc_now() >= RPC_BAD_UNTIL.get(url, 0):
            RPC_ROLE_INDEX[role] = (idx + 1) % n
            return url

    idx = start % n
    RPC_ROLE_INDEX[role] = (idx + 1) % n
    return RPC_HTTPS[idx]


def pick_rpc_ws():
    if not RPC_WSS:
        raise RuntimeError("NO_RPC_WS_CONFIG")

    start = RPC_ROLE_INDEX.get("ws", 0)
    n = len(RPC_WSS)

    for i in range(n):
        idx = (start + i) % n
        url = RPC_WSS[idx]
        if rpc_now() >= RPC_BAD_UNTIL.get(url, 0):
            RPC_ROLE_INDEX["ws"] = (idx + 1) % n
            return url

    idx = start % n
    RPC_ROLE_INDEX["ws"] = (idx + 1) % n
    return RPC_WSS[idx]


def mark_rpc_bad(url: str, cooldown: int = 20):
    RPC_BAD_UNTIL[url] = rpc_now() + cooldown


def is_rate_limit_error_text(s: str) -> bool:
    s = str(s).lower()
    return (
        "429" in s
        or "rate limit" in s
        or "too many requests" in s
        or "timeout" in s
        or "temporarily unavailable" in s
    )


async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        return await x
    return x


def candidate_meta(m):
    if m not in CANDIDATE_META:
        CANDIDATE_META[m] = {
            "first_seen": 0.0,
            "last_seen": 0.0,
            "hits": 0,
            "sources": set(),
            "source_last_seen": {},
        }
    return CANDIDATE_META[m]


def candidate_age_sec(m) -> float:
    meta = CANDIDATE_META.get(m)
    if not meta or not meta.get("last_seen"):
        return 10**9
    return now() - meta["last_seen"]


def candidate_recent_source(m, source: str, max_age: int) -> bool:
    meta = CANDIDATE_META.get(m, {})
    ts = meta.get("source_last_seen", {}).get(source, 0.0)
    return (now() - ts) <= max_age if ts else False


def source_quality_score(m: str) -> float:
    meta = CANDIDATE_META.get(m, {})
    sources = meta.get("sources", set())

    score = 0.0
    if "logs_sub" in sources:
        score += 0.018
    if "mempool" in sources:
        score += 0.020
    if "pump" in sources:
        score += 0.010
    if "jup" in sources:
        score += 0.004

    if len(sources) >= 2:
        score += 0.006
    if len(sources) >= 3:
        score += 0.006

    age = candidate_age_sec(m)
    if age <= 30:
        score += 0.015
    elif age <= 90:
        score += 0.008
    elif age > FAKE_SIGNAL_MAX_AGE_SEC:
        score -= 0.015

    return score


def is_fresh_sniper_candidate(m: str) -> bool:
    age = candidate_age_sec(m)
    return (
        age <= SNIPER_RECENT_WINDOW_SEC
        or candidate_recent_source(m, "mempool", SNIPER_RECENT_WINDOW_SEC)
        or candidate_recent_source(m, "logs_sub", SNIPER_RECENT_WINDOW_SEC)
        or candidate_recent_source(m, "pump", EARLY_SOURCE_WINDOW_SEC)
    )


async def signal_quality_ok(m: str, combo: float, a: float, w: float, s: float, liq_ok: bool, early_ok: bool):
    if m in {SOL, USDC, USDT}:
        return False, "BASE_TOKEN"

    age = candidate_age_sec(m)

    # 太舊而且沒有強來源，不做 sniper 類進場
    if age > FAKE_SIGNAL_MAX_AGE_SEC and w < 1.2 and not early_ok:
        return False, "STALE"

    # 沒流動性也沒早期流動性，直接拒絕
    if not liq_ok and not early_ok:
        return False, "NO_LIQ"

    # 新幣但沒有近期來源，容易是假訊號
    if m not in STATIC_UNIVERSE and not is_fresh_sniper_candidate(m) and w < 1.3:
        return False, "NO_FRESH_SOURCE"

    # alpha 很弱又沒有其他支持，跳過
    if combo < AI_PARAMS["entry_threshold"] and a <= 0 and s < 0.01 and w < 1.2:
        return False, "WEAK"

    return True, "OK"

# ================= HTTP =================
def jup_headers():
    headers = {"Accept": "application/json"}
    if JUP_API_KEY:
        headers["x-api-key"] = JUP_API_KEY
    return headers


async def http_get_json(url, params=None, headers=None):
    last_err = None

    for attempt in range(RPC_RETRY):
        try:
            async with RPC_SEM:
                r = await HTTP.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=RPC_TIMEOUT,
                )

            # ===== 成功 =====
            if r.status_code == 200:
                return r.json()

            last_err = f"GET {url} status={r.status_code}"

            # ===== 429 / rate limit =====
            if r.status_code == 429:
                await asyncio.sleep(1.5 + attempt)
                continue

            # ===== transient errors =====
            if r.status_code in (408, 425, 500, 502, 503, 504):
                await asyncio.sleep(min(1.5 * (attempt + 1), 5))
                continue

            return None

        except Exception as e:
            last_err = str(e)

            # ===== DNS error（Railway 常見）=====
            if "No address associated with hostname" in last_err:
                log_once("dns_err", f"DNS_FAIL {url}", 10)
                await asyncio.sleep(2)
                return None

            # ===== timeout / connection =====
            await asyncio.sleep(min(1.2 * (attempt + 1), 5))

    log_once(f"http_get_{url}", f"HTTP_GET_ERR {last_err}", 20)
    return None


async def http_post_json(url, payload=None, headers=None):
    last_err = None
    for attempt in range(RPC_RETRY):
        try:
            async with RPC_SEM:
                r = await HTTP.post(url, json=payload, headers=headers)

            if r.status_code == 200:
                return r.json()

            last_err = f"POST {url} status={r.status_code}"
            if r.status_code in (408, 425, 429, 500, 502, 503, 504):
                await asyncio.sleep(min(1.5 * (attempt + 1), 5))
                continue
            return None

        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(min(1.2 * (attempt + 1), 5))

    log_once(f"http_post_{url}", f"HTTP_POST_ERR {last_err}", 20)
    return None
# ================= JUP TOKEN FETCH (MULTI SOURCE) =================
async def fetch_jup_tokens():
    urls = [
        "https://lite-api.jup.ag/tokens",
        "https://cache.jup.ag/tokens",
        "https://token.jup.ag/all",
    ]

    for url in urls:
        data = await http_get_json(url)

        if isinstance(data, list) and len(data) > 0:
            return data

    return None

async def rpc_post(method: str, params, role: str = "default"):
    last_err = None

    for attempt in range(RPC_RETRY):
        rpc_url = pick_rpc_http(role)

        try:
            async with RPC_SEM:
                r = await HTTP.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    },
                    timeout=RPC_TIMEOUT,
                )

            # ===== 成功 =====
            if r.status_code == 200:
                data = r.json()

                # RPC error inside response
                if "error" in data and data["error"]:
                    err_txt = str(data["error"]).lower()

                    if "429" in err_txt or "rate limit" in err_txt:
                        mark_rpc_bad(rpc_url, cooldown=30 + attempt * 10)
                        await asyncio.sleep(1.5 + attempt)
                        continue

                return data.get("result")

            # ===== HTTP 429 =====
            if r.status_code == 429:
                mark_rpc_bad(rpc_url, cooldown=60)
                await asyncio.sleep(2 + attempt)
                continue

            # ===== transient =====
            if r.status_code in (408, 425, 500, 502, 503, 504):
                mark_rpc_bad(rpc_url, cooldown=30)
                await asyncio.sleep(1.5 + attempt)
                continue

            last_err = f"{rpc_url} status={r.status_code}"
            return None

        except Exception as e:
            last_err = f"{rpc_url} {e}"

            # ===== DNS / network =====
            if "No address associated with hostname" in last_err:
                mark_rpc_bad(rpc_url, cooldown=60)
                await asyncio.sleep(2)
                continue

            mark_rpc_bad(rpc_url, cooldown=30)
            await asyncio.sleep(1.2 + attempt)

    log_once(f"rpc_{method}", f"RPC_ERR {method} {last_err}", 20)
    return None

# ================= TOKEN META =================
async def preload_token_decimals():
    if TOKEN_DECIMALS:
        return

    data = await fetch_jup_tokens()

    if isinstance(data, list):
        for t in data:
            mint = t.get("address")
            if valid_mint(mint):
                TOKEN_DECIMALS[mint] = ensure_int(t.get("decimals"), 6)


def token_decimals(mint: str) -> int:
    return TOKEN_DECIMALS.get(mint, 6)
# ================= JUPITER ORDER (v1315 FINAL) =================
async def jupiter_order(input_mint: str, output_mint: str, amount_smallest: int):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount_smallest)),
        "swapMode": "ExactIn",
        "slippageBps": AI_PARAMS["slippage_bps"],
        "priorityFeeLamports": AI_PARAMS["priority_fee_lamports"],
    }

    if real_trading_ready():
        params["taker"] = wallet_pubkey_str()

    if USE_JITO and AI_PARAMS["jito_tip_lamports"] > 0:
        params["jitoTipLamports"] = AI_PARAMS["jito_tip_lamports"]

    data = await http_get_json(JUP_ORDER_API, params=params, headers=jup_headers())

    # ===== 成功 =====
    if data and not data.get("error") and not data.get("errorCode") and data.get("transaction"):
        return data

    # ===== fallback（只給 quote，不可 execute）=====
    log_once("jup_fallback", f"JUP_FALLBACK {input_mint[:4]}->{output_mint[:4]}", 10)

    try:
        quote = await http_get_json(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(int(amount_smallest)),
                "slippageBps": AI_PARAMS["slippage_bps"],
            },
        )

        if not quote or not quote.get("data"):
            return None

        route = quote["data"][0]

        return {
            "_quote_only": True,  # ❗關鍵
            "outAmount": route.get("outAmount"),
            "priceImpactPct": route.get("priceImpactPct"),
            "routePlan": route.get("routePlan"),
        }

    except Exception as e:
        log_once("jup_fallback_err", f"FALLBACK_ERR {e}", 20)

    return None

def sign_transaction_base64(tx_b64: str) -> str:
    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    msg_bytes = solders_message.to_bytes_versioned(raw_tx.message)
    taker_sig = KEYPAIR.sign_message(msg_bytes)

    existing_sigs = list(raw_tx.signatures)
    if existing_sigs:
        existing_sigs[0] = taker_sig
        signed_tx = VersionedTransaction.populate(raw_tx.message, existing_sigs)
    else:
        signed_tx = VersionedTransaction.populate(raw_tx.message, [taker_sig])

    return base64.b64encode(bytes(signed_tx)).decode("utf-8")


async def jupiter_execute(order: dict):
    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    last_valid_block_height = order.get("lastValidBlockHeight")

    if not tx_b64 or not request_id:
        raise RuntimeError(f"INVALID_ORDER tx={bool(tx_b64)} requestId={bool(request_id)}")

    signed_b64 = sign_transaction_base64(tx_b64)

    payload = {
        "signedTransaction": signed_b64,
        "requestId": request_id,
    }
    if last_valid_block_height is not None:
        try:
            payload["lastValidBlockHeight"] = int(last_valid_block_height)
        except Exception:
            pass

    result = await http_post_json(
        JUP_EXECUTE_API,
        payload,
        headers={**jup_headers(), "Content-Type": "application/json"},
    )

    if not result:
        raise RuntimeError("EXECUTE_HTTP_FAIL")

    status = result.get("status")
    signature = result.get("signature")
    code = result.get("code", None)
    error = result.get("error", "")

    if status != "Success":
        raise RuntimeError(f"EXECUTE_FAIL code={code} error={error} sig={signature}")

    return {
        "signature": signature,
        "result": result,
        "signed_transaction": signed_b64,
    }


async def confirm_signature(signature: str, timeout_sec: int = 35):
    deadline = now() + timeout_sec
    sleep_sec = 1.2

    while now() < deadline:
        result = await rpc_post(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
            role="confirm",
        )

        if result and result.get("value"):
            item = result["value"][0]
            if item:
                status = item.get("confirmationStatus")
                err = item.get("err")
                if err:
                    log_once(f"confirm_err_{signature}", f"CONFIRM_ERR {signature[:10]} {err}", 30)
                    return False
                if status in ("confirmed", "finalized"):
                    return True

        await asyncio.sleep(sleep_sec)
        sleep_sec = min(2.2, sleep_sec + 0.2)

    log_once(f"confirm_timeout_{signature}", f"CONFIRM_TIMEOUT {signature[:10]}", 30)
    return False


async def jito_send_bundle(serialized_txs):
    if not USE_JITO or not JITO_BUNDLE_URL:
        return {"ok": False, "reason": "JITO_DISABLED"}

    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [serialized_txs],
        }
        async with RPC_SEM:
            r = await HTTP.post(JITO_BUNDLE_URL, json=payload)
        if r.status_code != 200:
            return {"ok": False, "reason": f"http_{r.status_code}"}
        return {"ok": True, "result": r.json()}
    except Exception as e:
        return {"ok": False, "reason": str(e)}

# ================= MARKET =================
async def get_price(m):
    if m in PRICE_CACHE and now() - PRICE_CACHE[m][1] < 4:
        return PRICE_CACHE[m][0]

    data = await jupiter_order(m, SOL, 1_000_000)
    if not data:
        return None
    if data.get("errorCode") or data.get("error"):
        return None

    out_amount = ensure_int(data.get("outAmount"), 0)
    price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None
    PRICE_CACHE[m] = (price, now())
    return price


async def early_liquidity_ok(m):
    cache = EARLY_LIQ_CACHE.get(m)
    if cache and now() - cache["ts"] < 15:
        return cache["ok"]

    # 更早期、更小額，盡量不要一開始就 NO_LIQ
    test_sizes = [100_000, 250_000, 500_000, 1_000_000, 2_000_000]

    ok = False
    for amt in test_sizes:
        data = await jupiter_order(SOL, m, amt)
        if not data:
            continue
        if data.get("errorCode") or data.get("error"):
            continue

        out_amount = ensure_int(data.get("outAmount"), 0)
        price_impact = ensure_float(data.get("priceImpactPct"), 999)

        if out_amount >= EARLY_LIQ_MIN_OUT and price_impact < EARLY_LIQ_MAX_PRICE_IMPACT:
            ok = True
            break

    EARLY_LIQ_CACHE[m] = {"ok": ok, "ts": now()}
    return ok


async def liquidity_ok(m):
    cache = EARLY_LIQ_CACHE.get(f"strict:{m}")
    if cache and now() - cache["ts"] < 15:
        return cache["ok"]

    # 改成多段探測，避免 0.01 SOL 才測，導致新池一直 NO_LIQ
    test_sizes = [250_000, 500_000, 1_000_000, 2_000_000, 5_000_000]

    ok = False
    for amt in test_sizes:
        data = await jupiter_order(SOL, m, amt)
        if not data:
            continue
        if data.get("errorCode") or data.get("error"):
            continue

        out_amount = ensure_int(data.get("outAmount"), 0)
        price_impact = ensure_float(data.get("priceImpactPct"), 999)

        if out_amount >= MIN_STRICT_LIQ_OUT and price_impact < STRICT_LIQ_MAX_PRICE_IMPACT:
            ok = True
            break

    EARLY_LIQ_CACHE[f"strict:{m}"] = {"ok": ok, "ts": now()}
    return ok


async def anti_rug(m):
    # 兩段式檢查：能買到、也能小額賣回
    buy_side = await early_liquidity_ok(m)
    if not buy_side:
        return False

    test_sizes = [1, 10, 1000]
    for amt in test_sizes:
        data = await jupiter_order(m, SOL, amt)
        if not data:
            continue
        if data.get("errorCode") or data.get("error"):
            continue

        out_amount = ensure_int(data.get("outAmount"), 0)
        if out_amount >= MIN_SELLBACK_OUT:
            return True

    return False

# ================= WALLET GRAPH =================
async def build_wallet_graph():
    global LAST_WALLET_GRAPH_TS

    if now() - LAST_WALLET_GRAPH_TS < 30:
        return
    LAST_WALLET_GRAPH_TS = now()

    try:
        rpc_for_graph = pick_rpc_http("default")
        wallets = await extract_wallets_from_mints(rpc_for_graph, list(CANDIDATES)[-20:])
        behaviors = await track_wallet_behavior(rpc_for_graph, wallets)

        for item in behaviors:
            wallet = item["wallet"]
            tokens = item["tokens"]
            WALLET_GRAPH[wallet] = tokens
            WALLET_SCORE[wallet] = min(len(tokens) / 10.0, 1.5)

    except Exception as e:
        log_once("wallet_graph", f"WALLET_GRAPH_ERR {e}", 60)


def wallet_score(m):
    score = 1.0
    for wallet, tokens in WALLET_GRAPH.items():
        if m in tokens:
            score += WALLET_SCORE.get(wallet, 0.0)
    return min(score, 3.0)

# ================= TRUE MEMPOOL SNIPER =================
async def mempool_decode_loop():
    if not ENABLE_MEMPOOL_STREAM:
        log_once("mempool_stream_disabled", "MEMPOOL_STREAM_DISABLED", 3600)
        return

    backoff = 3
    while True:
        try:
            async def _cb(e):
                mint = None
                if isinstance(e, dict):
                    mint = e.get("mint")
                if valid_mint(mint):
                    RECENT_MEMPOOL_MINTS[mint] = now()
                    await add_candidate(mint, source="mempool")

            await _maybe_await(mempool_stream(_cb))
            backoff = 3
        except Exception as e:
            log_once("mempool_stream", f"MEMPOOL_STREAM_ERR {e}", 15)
            await asyncio.sleep(backoff)
            backoff = min(20, backoff * 2)


async def mempool_logs_subscribe_loop():
    if not ENABLE_MEMPOOL_LOGS:
        log_once("mempool_logs_disabled", "MEMPOOL_LOGS_DISABLED", 3600)
        return

    if not WATCH_PROGRAMS:
        log_once("mempool_logs_no_watch", "MEMPOOL_LOGS_NO_WATCH_PROGRAMS", 3600)
        return

    backoff = WS_BACKOFF_MIN

    while True:
        ws_url = None
        try:
            import websockets

            ws_url = pick_rpc_ws()

            async with websockets.connect(
                ws_url,
                ping_interval=15,
                ping_timeout=15,
                close_timeout=5,
                max_size=2**20,
            ) as ws:
                sub_ids = []

                for program_id in WATCH_PROGRAMS:
                    req = {
                        "jsonrpc": "2.0",
                        "id": len(sub_ids) + 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [program_id]},
                            {"commitment": "processed"},
                        ],
                    }
                    await ws.send(json.dumps(req))
                    resp = json.loads(await ws.recv())

                    if "error" in resp:
                        log_once("mempool_logs_method", f"LOGS_SUB_METHOD_ERR {resp['error']}", 300)
                        await asyncio.sleep(60)
                        break

                    if "result" in resp:
                        sub_ids.append(resp["result"])

                if not sub_ids:
                    await asyncio.sleep(30)
                    continue

                log_once("mempool_logs_ready", f"LOGS_SUB_READY {len(sub_ids)} via={ws_url}", 30)
                backoff = WS_BACKOFF_MIN

                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)
                    msg = json.loads(raw)

                    params = msg.get("params", {})
                    result = params.get("result", {})
                    value = result.get("value", {})
                    logs = value.get("logs", []) or []

                    for line in logs:
                        parts = str(line).replace(",", " ").replace(":", " ").split()
                        for part in parts:
                            if valid_mint(part):
                                RECENT_MEMPOOL_MINTS[part] = now()
                                await add_candidate(part, source="logs_sub")
                                break

        except Exception as e:
            if ws_url:
                mark_rpc_bad(ws_url, cooldown=30)
            log_once("mempool_logs_err", f"LOGS_SUB_ERR {e}", 15)
            await asyncio.sleep(backoff)
            backoff = min(WS_BACKOFF_MAX, backoff * 2)


async def sniper_bonus(m):
    cache = SNIPER_CACHE.get(m)
    if cache and now() - cache["ts"] < 8:
        return cache["value"]

    bonus = 0.0

    if m not in STATIC_UNIVERSE:
        bonus += 0.006

    if candidate_recent_source(m, "mempool", SNIPER_RECENT_WINDOW_SEC):
        bonus += 0.020
    if candidate_recent_source(m, "logs_sub", SNIPER_RECENT_WINDOW_SEC):
        bonus += 0.018
    if candidate_recent_source(m, "pump", EARLY_SOURCE_WINDOW_SEC):
        bonus += 0.010

    bonus += source_quality_score(m)

    if FORCE_EARLY_ENTRY and await early_liquidity_ok(m):
        bonus += EARLY_ENTRY_BONUS

    age = candidate_age_sec(m)
    if age > FAKE_SIGNAL_MAX_AGE_SEC:
        bonus -= 0.020

    bonus = max(0.0, bonus)
    SNIPER_CACHE[m] = {"value": bonus, "ts": now()}
    return bonus

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    if not p1 or p1 <= 0:
        return 0.0

    await asyncio.sleep(0.15)

    p2 = await get_price(m)
    if not p2 or p2 <= 0:
        return 0.0

    return (p2 - p1) / p1

# ================= STRATEGY =================
def pick_engine(combo):
    if combo > 0.03:
        return "sniper"
    elif combo > 0.015:
        return "degen"
    return "stable"


def position_size(combo):
    if combo > 0.03:
        return MAX_POSITION_SOL
    elif combo > 0.015:
        return MAX_POSITION_SOL * 0.7
    elif combo > 0.008:
        return MAX_POSITION_SOL * 0.5
    return MIN_POSITION_SOL


async def rank_candidates():
    pool = list(CANDIDATES)

    def sort_key(m):
        meta = CANDIDATE_META.get(m, {})
        return (meta.get("last_seen", 0.0), meta.get("hits", 0))

    pool.sort(key=sort_key, reverse=True)
    ranked = []

    # 不要一次跑太多，避免整個主循環卡住
    for m in pool[:15]:
        try:
            a = await alpha(m)
            w = wallet_score(m)
            s = await sniper_bonus(m)
            combo = a + (w * 0.01) + s
            ranked.append((m, combo, a, w, s))
        except Exception as e:
            log_once(f"rank_err_{m}", f"RANK_ERR {m[:6]} {e}", 20)
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:10]

    def sort_key(m):
        meta = CANDIDATE_META.get(m, {})
        last_seen = meta.get("last_seen", 0.0)
        hits = meta.get("hits", 0)
        return (last_seen, hits)

    pool.sort(key=sort_key, reverse=True)
    ranked = []

    for m in pool[:60]:
        try:
            a = await alpha(m)
            w = wallet_score(m)
            s = await sniper_bonus(m)
            combo = a + (w * 0.01) + s
            ranked.append((m, combo, a, w, s))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:15]

# ================= AI STRATEGY CULL =================
def strategy_stats_from_history():
    stats = {
        "stable": {"trades": 0, "wins": 0, "pnl": 0.0},
        "degen": {"trades": 0, "wins": 0, "pnl": 0.0},
        "sniper": {"trades": 0, "wins": 0, "pnl": 0.0},
    }

    for t in engine.trade_history[-100:]:
        eng = t.get("engine", "degen")
        if eng not in stats:
            continue
        pnl = ensure_float(t.get("pnl_pct"), 0.0)
        stats[eng]["trades"] += 1
        stats[eng]["pnl"] += pnl
        if pnl > 0:
            stats[eng]["wins"] += 1

    return stats


def review_strategies():
    global LAST_STRATEGY_REVIEW_TS

    if now() - LAST_STRATEGY_REVIEW_TS < 10:
        return
    LAST_STRATEGY_REVIEW_TS = now()

    stats = strategy_stats_from_history()

    for eng in ("stable", "degen", "sniper"):
        trades = stats[eng]["trades"]
        pnl = stats[eng]["pnl"]
        if trades >= 8 and pnl < -0.12:
            STRATEGY_ENABLED[eng] = False
        else:
            STRATEGY_ENABLED[eng] = True

    total_score = 0.0
    alloc_raw = {}
    for eng in ("stable", "degen", "sniper"):
        trades = max(stats[eng]["trades"], 1)
        pnl = stats[eng]["pnl"]
        winrate = stats[eng]["wins"] / trades
        score = max(0.1, 1.0 + pnl + winrate)
        if not STRATEGY_ENABLED[eng]:
            score = 0.05
        alloc_raw[eng] = score
        total_score += score

    engine.engine_allocator = {
        eng: alloc_raw[eng] / total_score for eng in alloc_raw
    }

# ================= CAPITAL LADDER =================
def capital_stage():
    capital = ensure_float(getattr(engine, "capital", 30.0), 30.0)

    if capital < 60:
        return "micro", 0.25
    if capital < 150:
        return "small", 0.35
    if capital < 500:
        return "growing", 0.50
    if capital < 1500:
        return "mid", 0.70
    return "large", 1.00


def capital_scale(size):
    _, mult = capital_stage()
    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, size * mult))

# ================= RISK =================
def risk_check():
    trades = engine.trade_history[-20:]
    if not trades:
        return False

    losses = [t for t in trades if ensure_float(t.get("pnl_pct"), 0.0) < 0]
    if len(losses) >= 5:
        return True

    avg = sum(ensure_float(t.get("pnl_pct"), 0.0) for t in trades) / len(trades)
    return avg < -0.05

# ================= EXEC =================
def can_buy(m):
    if m in {SOL, USDC, USDT}:
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p["token"] == m for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[m] < 30:
        return False
    return True


async def buy(m, a, combo, w, s):
    repair()

    if not can_buy(m):
        return

    engine_type = pick_engine(combo)
    if not STRATEGY_ENABLED.get(engine_type, True):
        log_once(f"eng_off_{engine_type}", f"STRATEGY_OFF {engine_type}", 30)
        return

    size = position_size(combo) * AI_PARAMS["size_multiplier"]

    if engine_type == "sniper":
        size *= 1.4
    elif engine_type == "stable":
        size *= 0.7

    alloc = engine.engine_allocator.get(engine_type, 0.33)
    size *= alloc
    size = capital_scale(size)
    size = max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))

    price = await get_price(m)
    if not price:
        return

    tx_sig = None
    tx_meta = None
    raw_token_amount = None
    trade_mode = "REAL" if real_trading_ready() else "PAPER"

    if real_trading_ready():
        lamports_in = int(size * 1e9)
        order = await jupiter_order(SOL, m, lamports_in)

        # ❗ fallback quote-only 防護（你現在缺這個）
        if order and order.get("_quote_only"):
            log_once("buy_quote_only", f"BUY_QUOTE_ONLY {m[:6]}", 10)
            return

        if not order:
            log_once("buy_order", f"BUY_ORDER_ERR {m[:6]} no_response", 15)
            return

        if order.get("errorCode") or order.get("error"):
            log_once(
                "buy_order",
                f"BUY_ORDER_FAIL {m[:6]} {order.get('errorMessage') or order.get('error')}",
                15,
            )
            return

        if not order.get("transaction") or not order.get("requestId"):
            log_once("buy_order", f"BUY_ORDER_NO_TX {m[:6]}", 15)
            return

        raw_token_amount = ensure_int(order.get("outAmount"), 0)

        try:
            exec_result = await jupiter_execute(order)
            tx_sig = exec_result.get("signature")
            tx_meta = exec_result.get("result")

            if USE_JITO and JITO_BUNDLE_URL and exec_result.get("signed_transaction"):
                asyncio.create_task(jito_send_bundle([exec_result["signed_transaction"]]))

        except Exception as e:
            log_once("buy_exec", f"BUY_EXEC_ERR {m[:6]} {e}", 15)
            return

        if not tx_sig:
            log_once("buy_exec", f"BUY_EXEC_FAIL {m[:6]}", 15)
            return

        await confirm_signature(tx_sig)

    pos = {
        "token": m,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0.0,
        "amount": size / price,
        "raw_token_amount": raw_token_amount,
        "engine": engine_type,
        "alpha": a,
        "entry_ts": now(),
        "wallet_score": w,
        "sniper_score": s,
        "combo": combo,
        "trade_mode": trade_mode,
        "entry_signature": tx_sig,
        "entry_tx_meta": tx_meta,
    }

    engine.positions.append(pos)

    TOKEN_COOLDOWN[m] = now()
    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {m[:6]}"

    log(f"BUY {m[:6]} {engine_type} combo={combo:.4f} size={size:.6f} mode={trade_mode} sig={tx_sig}")


async def sell(p):
    repair()

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) / p["entry_price"]

    tx_sig = None
    tx_meta = None

    if real_trading_ready():
        raw_amount = ensure_int(p.get("raw_token_amount"), 0)

        if raw_amount <= 0:
            raw_amount = int(
                max(0, ensure_float(p.get("amount"), 0.0)) * (10 ** token_decimals(p["token"]))
            )

        order = await jupiter_order(p["token"], SOL, raw_amount)

        # ❗ fallback quote-only 防護
        if order and order.get("_quote_only"):
            log_once("sell_quote_only", f"SELL_QUOTE_ONLY {p['token'][:6]}", 10)
            return

        if order and order.get("transaction") and order.get("requestId") and not order.get("errorCode") and not order.get("error"):
            try:
                exec_result = await jupiter_execute(order)
                tx_sig = exec_result.get("signature")
                tx_meta = exec_result.get("result")

                if USE_JITO and JITO_BUNDLE_URL and exec_result.get("signed_transaction"):
                    asyncio.create_task(jito_send_bundle([exec_result["signed_transaction"]]))

                if tx_sig:
                    await confirm_signature(tx_sig)

            except Exception as e:
                log_once("sell_exec", f"SELL_EXEC_ERR {p['token'][:6]} {e}", 15)
        else:
            log_once("sell_order", f"SELL_ORDER_ERR {p['token'][:6]}", 15)

    try:
        engine.positions.remove(p)
    except ValueError:
        return

    engine.trade_history.append({
        "token": p["token"],
        "pnl_pct": pnl,
        "ts": now(),
    })

    engine.stats["sells"] += 1

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
                p["last_price"] = price
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak if peak else 0.0
                stop = -AI_PARAMS["trailing_stop"]

                if pnl < stop or drawdown < stop:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log_once("monitor", f"MONITOR_ERR {e}", 30)

        await asyncio.sleep(6)

# ================= AI LOOP =================
async def ai_loop():
    while True:
        try:
            trades = engine.trade_history[-30:]
            if trades:
                avg = sum(ensure_float(t.get("pnl_pct"), 0.0) for t in trades) / len(trades)

                if avg > 0:
                    AI_PARAMS["entry_threshold"] *= 0.98
                    AI_PARAMS["size_multiplier"] *= 1.05
                    AI_PARAMS["trailing_stop"] *= 0.98
                    AI_PARAMS["slippage_bps"] = min(150, AI_PARAMS["slippage_bps"] + 5)
                else:
                    AI_PARAMS["entry_threshold"] *= 1.05
                    AI_PARAMS["size_multiplier"] *= 0.95
                    AI_PARAMS["trailing_stop"] *= 1.03
                    AI_PARAMS["slippage_bps"] = max(30, AI_PARAMS["slippage_bps"] - 5)

                AI_PARAMS["entry_threshold"] = min(0.02, max(0.001, AI_PARAMS["entry_threshold"]))
                AI_PARAMS["size_multiplier"] = min(2.0, max(0.3, AI_PARAMS["size_multiplier"]))
                AI_PARAMS["trailing_stop"] = min(0.15, max(0.03, AI_PARAMS["trailing_stop"]))

            review_strategies()

        except Exception as e:
            log_once("ai", f"AI_ERR {e}", 30)

        await asyncio.sleep(10)

# ================= SOURCES =================
async def add_candidate(m, source="unknown"):
    if not valid_mint(m):
        return

    CANDIDATES.add(m)
    engine.stats["adds"] += 1

    meta = candidate_meta(m)
    if not meta["first_seen"]:
        meta["first_seen"] = now()
    meta["last_seen"] = now()
    meta["hits"] += 1
    meta["sources"].add(source)
    meta["source_last_seen"][source] = now()

    if source in {"mempool", "logs_sub"}:
        RECENT_MEMPOOL_MINTS[m] = now()

    log_once(f"cand_{m}", f"ADD {m[:6]} src={source}", 120)


async def pump():
    while True:
        data = await http_get_json(PUMP_API)
        if isinstance(data, list):
            for c in data[:20]:
                m = c.get("mint")
                if valid_mint(m):
                    await add_candidate(m, source="pump")
        await asyncio.sleep(10)


async def jup():
    while True:
        data = await fetch_jup_tokens()

        if isinstance(data, list):
            for t in data[:50]:
                m = t.get("address")
                if valid_mint(m):
                    TOKEN_DECIMALS[m] = ensure_int(
                        t.get("decimals"),
                        TOKEN_DECIMALS.get(m, 6)
                    )
                    await add_candidate(m, source="jup")

        await asyncio.sleep(120)

# ================= MAIN =================
async def main():
    repair()
    await preload_token_decimals()

    mode = "REAL" if real_trading_ready() else "PAPER"
    log(
        f"🚀 v1314 START mode={mode} "
        f"http_rpcs={len(RPC_HTTPS)} ws_rpcs={len(RPC_WSS)} "
        f"use_jito={USE_JITO}"
    )

    if REAL_TRADING and not real_trading_ready():
        log("REAL_TRADING requested but PRIVATE_KEY_B58/PRIVATE_KEY_JSON or JUP_API_KEY invalid; falling back to PAPER")

    for m in FALLBACK_TOKENS:
        await add_candidate(m, source="fallback")

    asyncio.create_task(pump())
    asyncio.create_task(jup())
    asyncio.create_task(mempool_decode_loop())
    asyncio.create_task(mempool_logs_subscribe_loop())
    asyncio.create_task(monitor())
    asyncio.create_task(ai_loop())

    while True:
        try:
            repair()

            if risk_check():
                log("⛔ RISK STOP")
                await asyncio.sleep(30)
                continue

            await build_wallet_graph()

            engine.candidate_count = len(CANDIDATES)
            log_once("cand_count", f"CANDIDATE_COUNT {engine.candidate_count}", 10)

            ranked = await rank_candidates()
            log_once("ranked_count", f"RANKED_COUNT {len(ranked)}", 10)
            for m, combo, a, w, s in ranked:
                engine.stats["signals"] += 1

                liq_ok = await liquidity_ok(m)
                early_ok = False

                if not liq_ok and FORCE_EARLY_ENTRY:
                    early_ok = await early_liquidity_ok(m)

                if not liq_ok and not early_ok:
                    log_once(
                        f"skip_liq_{m}",
                        f"SKIP {m[:6]} NO_LIQ combo={combo:.4f}",
                        30,
                    )
                    continue

                rug_ok = await anti_rug(m)
                if not rug_ok:
                    log_once(
                        f"skip_rug_{m}",
                        f"SKIP {m[:6]} RUG_FAIL combo={combo:.4f}",
                        30,
                    )
                    continue

                effective_combo = combo + (EARLY_ENTRY_BONUS if early_ok and not liq_ok else 0.0)

                ok_signal, reason = await signal_quality_ok(
                    m=m,
                    combo=effective_combo,
                    a=a,
                    w=w,
                    s=s,
                    liq_ok=liq_ok,
                    early_ok=early_ok,
                )
                if not ok_signal:
                    log_once(
                        f"skip_fake_{m}",
                        f"SKIP {m[:6]} SIGNAL_{reason} combo={effective_combo:.4f}",
                        30,
                    )
                    continue

                engine.last_signal = (
                    f"{m[:6]} a={a:.4f} w={w:.2f} s={s:.4f} "
                    f"c={effective_combo:.4f} thr={AI_PARAMS['entry_threshold']:.4f}"
                )

                if effective_combo > AI_PARAMS["entry_threshold"]:
                    log_once(
                        f"try_buy_{m}",
                        f"TRY_BUY {m[:6]} combo={effective_combo:.4f} thr={AI_PARAMS['entry_threshold']:.4f}",
                        15,
                    )
                    await buy(m, a, effective_combo, w, s)
                else:
                    log_once(
                        f"skip_thr_{m}",
                        f"SKIP {m[:6]} THRESHOLD combo={effective_combo:.4f} thr={AI_PARAMS['entry_threshold']:.4f}",
                        30,
                    )

            await asyncio.sleep(6)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.bot_ok = False
            engine.bot_error = str(e)
            log(f"ERR {e}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    await main()

# ================= PATCH v1314.1 (NO FEATURE REMOVED) =================

# ===== 防重複下單 =====
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

async def safe_jupiter_order(input_mint, output_mint, amount):
    for _ in range(3):
        data = await jupiter_order(input_mint, output_mint, amount)

        # ❗避免 quote-only 被當成成功
        if data and data.get("transaction"):
            return data

        await asyncio.sleep(0.3)

    return None


async def safe_jupiter_execute(order):
    for _ in range(2):
        try:
            return await jupiter_execute(order)
        except Exception:
            await asyncio.sleep(0.5)

    raise RuntimeError("EXECUTE_FAIL_RETRY")


# ================= PATCH BUY =================
_original_buy = buy

async def buy(m, a, combo, w, s):
    if m in IN_FLIGHT_BUY:
        return
    IN_FLIGHT_BUY.add(m)

    try:
        repair()

        if not can_buy(m):
            return

        engine_type = pick_engine(combo)
        if not STRATEGY_ENABLED.get(engine_type, True):
            return

        size = position_size(combo) * AI_PARAMS["size_multiplier"]

        if engine_type == "sniper":
            size *= 1.4
        elif engine_type == "stable":
            size *= 0.7

        alloc = engine.engine_allocator.get(engine_type, 0.33)
        size *= alloc
        size = capital_scale(size)
        size = max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))

        price = await get_price(m)
        if not price:
            return

        tx_sig = None
        raw_token_amount = None
        trade_mode = "REAL" if real_trading_ready() else "PAPER"

        if real_trading_ready():
            lamports_in = int(size * 1e9)

            order = await safe_jupiter_order(SOL, m, lamports_in)
            if not order:
                log_once("buy_fail", f"BUY_FAIL {m[:6]}", 10)
                return

            raw_token_amount = ensure_int(order.get("outAmount"), 0)

            try:
                exec_result = await safe_jupiter_execute(order)
                tx_sig = exec_result.get("signature")

                if USE_JITO and exec_result.get("signed_transaction"):
                    asyncio.create_task(jito_send_bundle([exec_result["signed_transaction"]]))

            except Exception as e:
                log_once("buy_exec", f"BUY_EXEC_ERR {m[:6]} {e}", 10)
                return

            if tx_sig:
                await confirm_signature(tx_sig)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl_pct": 0.0,
            "amount": size / price,
            "raw_token_amount": raw_token_amount,
            "engine": engine_type,
            "alpha": a,
            "entry_ts": now(),
            "wallet_score": w,
            "sniper_score": s,
            "combo": combo,
            "trade_mode": trade_mode,
            "entry_signature": tx_sig,
        })

        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1

        log(f"BUY {m[:6]} size={size:.6f} sig={tx_sig}")

    finally:
        IN_FLIGHT_BUY.discard(m)



# ================= PATCH SELL =================
_original_sell = sell

async def sell(p):
    m = p["token"]

    if m in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(m)

    try:
        repair()

        price = await get_price(m)
        if not price:
            return

        pnl = (price - p["entry_price"]) / p["entry_price"]

        tx_sig = None

        if real_trading_ready():
            raw_amount = ensure_int(p.get("raw_token_amount"), 0)

            if raw_amount <= 0:
                raw_amount = int(
                    max(0, ensure_float(p.get("amount"), 0.0)) * (10 ** token_decimals(m))
                )

            order = await safe_jupiter_order(m, SOL, raw_amount)

            if order:
                try:
                    exec_result = await safe_jupiter_execute(order)
                    tx_sig = exec_result.get("signature")

                    if USE_JITO and exec_result.get("signed_transaction"):
                        asyncio.create_task(jito_send_bundle([exec_result["signed_transaction"]]))

                    if tx_sig:
                        await confirm_signature(tx_sig)

                except Exception:
                    pass

        try:
            engine.positions.remove(p)
        except ValueError:
            return

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now(),
        })

        engine.stats["sells"] += 1

        log(f"SELL {m[:6]} pnl={pnl:.4f} sig={tx_sig}")

    finally:
        IN_FLIGHT_SELL.discard(m)

# ================= SMART MONEY =================
SMART_MONEY = {}
SMART_MONEY_SCORE = {}

def update_smart_money():
    for t in engine.trade_history[-200:]:
        wallet = t.get("wallet")
        pnl = ensure_float(t.get("pnl_pct"), 0)

        if not wallet:
            continue

        if wallet not in SMART_MONEY:
            SMART_MONEY[wallet] = {"pnl": 0.0, "trades": 0}

        SMART_MONEY[wallet]["pnl"] += pnl
        SMART_MONEY[wallet]["trades"] += 1

    for w, s in SMART_MONEY.items():
        if s["trades"] >= 3:
            SMART_MONEY_SCORE[w] = max(0.5, min(3.0, 1 + s["pnl"]))


def smart_money_score(m):
    score = 1.0
    for wallet, tokens in WALLET_GRAPH.items():
        if m in tokens:
            score += SMART_MONEY_SCORE.get(wallet, 0.0)
    return min(score, 4.0)


# ================= INSIDER DETECT =================
def insider_score(m):
    meta = CANDIDATE_META.get(m, {})
    age = candidate_age_sec(m)

    score = 0.0

    if age < 20:
        score += 0.03

    if meta.get("hits", 0) <= 2:
        score += 0.02

    if "mempool" in meta.get("sources", []):
        score += 0.03

    return score


# ================= CLUSTER DETECT =================
def cluster_score(m):
    count = 0
    for wallet, tokens in WALLET_GRAPH.items():
        if m in tokens:
            count += 1

    if count >= 3:
        return 0.03
    if count >= 2:
        return 0.015
    return 0.0


# ================= FAKE PUMP FILTER =================
def fake_pump_filter(m, a, w, s):
    if a > 0.05 and w < 1.1 and s < 0.01:
        return False

    if w < 1.05 and s < 0.01:
        return False

    return True


# ================= TAKE PROFIT AI =================
async def take_profit_logic(p):
    price = await get_price(p["token"])
    if not price:
        return False

    pnl = (price - p["entry_price"]) / p["entry_price"]

    # 分段止盈
    if pnl > 0.25:
        return True
    if pnl > 0.15 and p["sniper_score"] < 0.01:
        return True
    if pnl > 0.10 and p["wallet_score"] < 1.2:
        return True

    return False


# ================= PATCH RANK =================
_original_rank = rank_candidates

async def rank_candidates():
    update_smart_money()

    ranked = await _original_rank()
    new_ranked = []

    for m, combo, a, w, s in ranked:
        sm = smart_money_score(m)
        ins = insider_score(m)
        cl = cluster_score(m)

        combo2 = combo + (sm * 0.01) + ins + cl

        new_ranked.append((m, combo2, a, w, s))

    new_ranked.sort(key=lambda x: x[1], reverse=True)
    return new_ranked


# ================= PATCH BUY FILTER =================
_original_can_buy = can_buy

def can_buy(m):
    if not _original_can_buy(m):
        return False

    meta = CANDIDATE_META.get(m, {})
    a = 0
    w = wallet_score(m)
    s = 0

    if not fake_pump_filter(m, a, w, s):
        return False

    return True


# ================= PATCH MONITOR =================
_original_monitor = monitor

async def monitor():
    while True:
        try:
            for p in list(engine.positions):

                if await take_profit_logic(p):
                    await sell(p)
                    continue

                price = await get_price(p["token"])
                if not price:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)

                p["peak_price"] = peak
                p["last_price"] = price
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak if peak else 0.0

                if pnl < -AI_PARAMS["trailing_stop"] or drawdown < -AI_PARAMS["trailing_stop"]:
                    await sell(p)

        except Exception as e:
            log_once("monitor_v1315", f"MONITOR_ERR {e}", 30)

        await asyncio.sleep(5)
