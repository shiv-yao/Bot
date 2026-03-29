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
JUP_TOKENS_API = os.environ.get("JUP_TOKENS_API", "https://token.jup.ag/all")
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
EARLY_LIQ_MAX_PRICE_IMPACT = float(os.environ.get("EARLY_LIQ_MAX_PRICE_IMPACT", "0.60"))
STRICT_LIQ_MAX_PRICE_IMPACT = float(os.environ.get("STRICT_LIQ_MAX_PRICE_IMPACT", "0.30"))

FORCE_EARLY_ENTRY = os.environ.get("FORCE_EARLY_ENTRY", "true").lower() == "true"
EARLY_ENTRY_BONUS = float(os.environ.get("EARLY_ENTRY_BONUS", "0.012"))

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
SNIPER_CACHE = set()
RECENT_MEMPOOL_MINTS = {}
EARLY_LIQ_CACHE = {}
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
                r = await HTTP.get(url, params=params, headers=headers)

            if r.status_code == 200:
                return r.json()

            last_err = f"GET {url} status={r.status_code}"
            if r.status_code in (408, 425, 429, 500, 502, 503, 504):
                await asyncio.sleep(min(1.5 * (attempt + 1), 5))
                continue
            return None

        except Exception as e:
            last_err = str(e)
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


async def rpc_post(method: str, params, role: str = "default"):
    last_err = None

    for attempt in range(RPC_RETRY):
        rpc_url = pick_rpc_http(role)

        try:
            async with RPC_SEM:
                r = await HTTP.post(
                    rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=RPC_TIMEOUT,
                )

            if r.status_code == 200:
                data = r.json()
                if "error" in data and data["error"]:
                    err_txt = str(data["error"])
                    if is_rate_limit_error_text(err_txt):
                        mark_rpc_bad(rpc_url, cooldown=20 + attempt * 5)
                        await asyncio.sleep(min(1.0 * (attempt + 1), 4))
                        continue
                return data.get("result")

            last_err = f"{rpc_url} status={r.status_code}"
            if r.status_code in (408, 425, 429, 500, 502, 503, 504):
                mark_rpc_bad(rpc_url, cooldown=20 + attempt * 5)
                await asyncio.sleep(min(1.0 * (attempt + 1), 4))
                continue

            return None

        except Exception as e:
            last_err = f"{rpc_url} {e}"
            mark_rpc_bad(rpc_url, cooldown=20 + attempt * 5)
            await asyncio.sleep(min(1.0 * (attempt + 1), 4))

    log_once(f"rpc_{method}", f"RPC_ERR {method} {last_err}", 20)
    return None

# ================= TOKEN META =================
async def preload_token_decimals():
    if TOKEN_DECIMALS:
        return
    data = await http_get_json(JUP_TOKENS_API)
    if isinstance(data, list):
        for t in data:
            mint = t.get("address")
            if valid_mint(mint):
                TOKEN_DECIMALS[mint] = ensure_int(t.get("decimals"), 6)


def token_decimals(mint: str) -> int:
    return TOKEN_DECIMALS.get(mint, 6)

# ================= JUPITER ORDER / EXECUTE =================
async def jupiter_order(input_mint: str, output_mint: str, amount_smallest: int):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount_smallest)),
        "taker": wallet_pubkey_str() if real_trading_ready() else None,
        "swapMode": "ExactIn",
        "slippageBps": AI_PARAMS["slippage_bps"],
        "priorityFeeLamports": AI_PARAMS["priority_fee_lamports"],
    }

    if USE_JITO and AI_PARAMS["jito_tip_lamports"] > 0:
        params["jitoTipLamports"] = AI_PARAMS["jito_tip_lamports"]

    params = {k: v for k, v in params.items() if v is not None}
    return await http_get_json(JUP_ORDER_API, params=params, headers=jup_headers())


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

    test_sizes = [500_000, 1_000_000, 2_000_000]

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

    test_sizes = [2_000_000, 5_000_000, 10_000_000]

    ok = False
    for amt in test_sizes:
        data = await jupiter_order(SOL, m, amt)
        if not data:
            continue
        if data.get("errorCode") or data.get("error"):
            continue

        out_amount = ensure_int(data.get("outAmount"), 0)
        price_impact = ensure_float(data.get("priceImpactPct"), 999)

        if out_amount > 0 and price_impact < STRICT_LIQ_MAX_PRICE_IMPACT:
            ok = True
            break

    EARLY_LIQ_CACHE[f"strict:{m}"] = {"ok": ok, "ts": now()}
    return ok


async def anti_rug(m):
    data = await jupiter_order(m, SOL, 1_000_000)
    if not data:
        return False
    if data.get("errorCode") or data.get("error"):
        return False
    return ensure_int(data.get("outAmount"), 0) > 0

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
    if not WATCH_PROGRAMS:
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
                    if "result" in resp:
                        sub_ids.append(resp["result"])

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
    if m in SNIPER_CACHE:
        return 0.0

    bonus = 0.0
    if m not in STATIC_UNIVERSE:
        bonus += 0.01 + random.random() * 0.01

    if m in RECENT_MEMPOOL_MINTS and now() - RECENT_MEMPOOL_MINTS[m] < 30:
        bonus += 0.02

    if FORCE_EARLY_ENTRY and await early_liquidity_ok(m):
        bonus += EARLY_ENTRY_BONUS

    if bonus > 0:
        SNIPER_CACHE.add(m)

    return bonus

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(1)
    p2 = await get_price(m)
    if not p1 or not p2 or p1 <= 0:
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
    ranked = []
    for m in list(CANDIDATES)[-40:]:
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

        if not order:
            log_once("buy_order", f"BUY_ORDER_ERR {m[:6]} no_response", 15)
            return

        if order.get("errorCode") or order.get("error"):
            log_once(
                "buy_order",
                f"BUY_ORDER_FAIL {m[:6]} {order.get('errorMessage') or order.get('error') or order.get('errorCode')}",
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
    engine.last_signal = (
        f"{m[:6]} a={a:.4f} w={w:.2f} s={s:.4f} c={combo:.4f} eng={engine_type}"
    )

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

    trade = {
        "token": p["token"],
        "pnl_pct": pnl,
        "ts": now(),
        "engine": p.get("engine", "degen"),
        "entry_price": p.get("entry_price"),
        "exit_price": price,
        "alpha": p.get("alpha", 0.0),
        "combo": p.get("combo", 0.0),
        "wallet_score": p.get("wallet_score", 0.0),
        "sniper_score": p.get("sniper_score", 0.0),
        "trade_mode": p.get("trade_mode", "PAPER"),
        "entry_signature": p.get("entry_signature"),
        "exit_signature": tx_sig,
        "exit_tx_meta": tx_meta,
    }
    engine.trade_history.append(trade)

    eng = p.get("engine", "degen")
    STRATEGY_LOCAL_STATS[eng]["trades"] += 1
    STRATEGY_LOCAL_STATS[eng]["pnl"] += pnl
    engine.engine_stats[eng]["trades"] += 1
    engine.engine_stats[eng]["pnl"] += pnl
    if pnl > 0:
        STRATEGY_LOCAL_STATS[eng]["wins"] += 1
        engine.engine_stats[eng]["wins"] += 1

    engine.capital = max(1.0, ensure_float(engine.capital, 30.0) * (1.0 + pnl * 0.1))
    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {p['token'][:6]}"
    log(f"SELL {p['token'][:6]} pnl={pnl:.4f} eng={eng} mode={p.get('trade_mode', 'PAPER')} sig={tx_sig}")

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
    if valid_mint(m):
        CANDIDATES.add(m)
        engine.stats["adds"] += 1
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
        data = await http_get_json(JUP_TOKENS_API)
        if isinstance(data, list):
            for t in data[:50]:
                m = t.get("address")
                if valid_mint(m):
                    TOKEN_DECIMALS[m] = ensure_int(t.get("decimals"), TOKEN_DECIMALS.get(m, 6))
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
            ranked = await rank_candidates()

            engine.candidate_count = len(CANDIDATES)

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
