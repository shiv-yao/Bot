# ================= v1314 REAL TRADING (JUP V2 ORDER + SIGN + SEND) =================
import os
import json
import time
import base64
import random
import asyncio
from collections import defaultdict

import httpx

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

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_TOKENS_API = "https://token.jup.ag/all"
JUP_ORDER_API = "https://api.jup.ag/swap/v2/order"

RPC_HTTP = os.environ.get("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
RPC_WS = os.environ.get("SOLANA_RPC_WS", "wss://api.mainnet-beta.solana.com")
JITO_BUNDLE_URL = os.environ.get("JITO_BUNDLE_URL", "")
USE_JITO = os.environ.get("USE_JITO", "false").lower() == "true"

REAL_TRADING = os.environ.get("REAL_TRADING", "false").lower() == "true"
JUP_API_KEY = os.environ.get("JUP_API_KEY", "").strip()

PRIVATE_KEY_JSON = os.environ.get("PRIVATE_KEY_JSON", "").strip()
KEYPAIR = None
if PRIVATE_KEY_JSON:
    try:
        KEYPAIR = Keypair.from_bytes(bytes(json.loads(PRIVATE_KEY_JSON)))
    except Exception:
        KEYPAIR = None

HTTP = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

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

# ================= UTIL =================
def now() -> float:
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

# ================= HTTP =================
def jup_headers():
    headers = {"Accept": "application/json"}
    if JUP_API_KEY:
        headers["x-api-key"] = JUP_API_KEY
    return headers

async def http_get_json(url, params=None, headers=None):
    try:
        r = await HTTP.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

async def http_post_json(url, payload=None, headers=None):
    try:
        r = await HTTP.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

async def rpc_post(method: str, params):
    try:
        r = await HTTP.post(
            RPC_HTTP,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("result")
    except Exception:
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

# ================= JUPITER ORDER =================
async def jupiter_order(input_mint: str, output_mint: str, amount_smallest: int):
    """
    Latest Jupiter v2 /order:
    returns quote + assembled base64 tx when taker is provided.
    """
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
    signature = KEYPAIR.sign_message(solders_message.to_bytes_versioned(raw_tx.message))
    signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
    return base64.b64encode(bytes(signed_tx)).decode("utf-8")

async def send_signed_transaction_b64(tx_b64: str):
    return await rpc_post(
        "sendTransaction",
        [
            tx_b64,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "processed",
                "maxRetries": 5,
            },
        ],
    )

async def confirm_signature(signature: str, timeout_sec: int = 35):
    deadline = now() + timeout_sec
    while now() < deadline:
        result = await rpc_post(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
        )
        if result and result.get("value"):
            item = result["value"][0]
            if item and item.get("confirmationStatus") in ("confirmed", "finalized"):
                return True
        await asyncio.sleep(1.5)
    return False

# optional hook, kept
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

    out_amount = ensure_int(data.get("outAmount"), 0)
    price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None
    PRICE_CACHE[m] = (price, now())
    return price

async def liquidity_ok(m):
    data = await jupiter_order(SOL, m, 10_000_000)
    return bool(data and ensure_int(data.get("outAmount"), 0) > 5000)

async def anti_rug(m):
    data = await jupiter_order(m, SOL, 1_000_000)
    return bool(data and ensure_int(data.get("outAmount"), 0) > 0)

# ================= WALLET GRAPH =================
async def build_wallet_graph():
    global LAST_WALLET_GRAPH_TS

    if now() - LAST_WALLET_GRAPH_TS < 30:
        return
    LAST_WALLET_GRAPH_TS = now()

    try:
        wallets = await extract_wallets_from_mints(RPC_HTTP, list(CANDIDATES)[-20:])
        behaviors = await track_wallet_behavior(RPC_HTTP, wallets)

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
SNIPER_CACHE = set()
RECENT_MEMPOOL_MINTS = {}
WATCH_PROGRAMS = set(filter(None, [
    os.environ.get("WATCH_PROGRAM_1", ""),
    os.environ.get("WATCH_PROGRAM_2", ""),
]))

async def mempool_decode_loop():
    while True:
        try:
            await mempool_stream(lambda e: add_candidate(e.get("mint"), source="mempool"))
        except Exception as e:
            log_once("mempool_stream", f"MEMPOOL_STREAM_ERR {e}", 30)
            await asyncio.sleep(5)

async def mempool_logs_subscribe_loop():
    if not WATCH_PROGRAMS:
        return

    while True:
        try:
            import websockets

            async with websockets.connect(RPC_WS, ping_interval=20, ping_timeout=20) as ws:
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

                log_once("mempool_logs", f"LOGS_SUB_READY {len(sub_ids)}", 60)

                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    params = msg.get("params", {})
                    result = params.get("result", {})
                    value = result.get("value", {})
                    logs = value.get("logs", []) or []

                    for line in logs:
                        parts = str(line).split()
                        for part in parts:
                            if valid_mint(part):
                                RECENT_MEMPOOL_MINTS[part] = now()
                                await add_candidate(part, source="logs_sub")
                                break

        except Exception as e:
            log_once("mempool_logs", f"LOGS_SUB_ERR {e}", 30)
            await asyncio.sleep(5)

async def sniper_bonus(m):
    if m in SNIPER_CACHE:
        return 0.0

    bonus = 0.0
    if m not in STATIC_UNIVERSE:
        bonus += 0.01 + random.random() * 0.01

    if m in RECENT_MEMPOOL_MINTS and now() - RECENT_MEMPOOL_MINTS[m] < 30:
        bonus += 0.02

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
    for m in list(CANDIDATES):
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

        if not order or not order.get("transaction"):
            log_once("buy_order", f"BUY_ORDER_ERR {m[:6]}", 15)
            return

        if order.get("errorCode") or order.get("error"):
            log_once("buy_order", f"BUY_ORDER_FAIL {m[:6]} {order.get('errorMessage') or order.get('error')}", 15)
            return

        raw_token_amount = ensure_int(order.get("outAmount"), 0)

        try:
            signed_b64 = sign_transaction_base64(order["transaction"])
        except Exception as e:
            log_once("buy_sign", f"BUY_SIGN_ERR {m[:6]} {e}", 15)
            return

        if USE_JITO and JITO_BUNDLE_URL:
            tx_meta = await jito_send_bundle([signed_b64])

        # retry send
        for _ in range(2):
            tx_sig = await send_signed_transaction_b64(signed_b64)
            if tx_sig:
                break
            await asyncio.sleep(0.5)

        if not tx_sig:
            log_once("buy_send", f"BUY_SEND_ERR {m[:6]}", 15)
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

    log(f"BUY {m[:6]} {engine_type} combo={combo:.4f} size={size:.6f} mode={trade_mode}")

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

        # fallback
        if raw_amount <= 0:
            raw_amount = int(max(0, ensure_float(p.get("amount"), 0.0)) * (10 ** token_decimals(p["token"])))

        order = await jupiter_order(p["token"], SOL, raw_amount)
        if order and order.get("transaction") and not order.get("errorCode") and not order.get("error"):
            try:
                signed_b64 = sign_transaction_base64(order["transaction"])
                if USE_JITO and JITO_BUNDLE_URL:
                    tx_meta = await jito_send_bundle([signed_b64])

                for _ in range(2):
                    tx_sig = await send_signed_transaction_b64(signed_b64)
                    if tx_sig:
                        break
                    await asyncio.sleep(0.5)

                if tx_sig:
                    await confirm_signature(tx_sig)
            except Exception as e:
                log_once("sell_real", f"SELL_REAL_ERR {p['token'][:6]} {e}", 15)
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
    log(f"SELL {p['token'][:6]} pnl={pnl:.4f} eng={eng} mode={p.get('trade_mode', 'PAPER')}")

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
    log(f"🚀 v1314 START mode={mode}")

    if REAL_TRADING and not real_trading_ready():
        log("REAL_TRADING requested but PRIVATE_KEY_JSON or JUP_API_KEY invalid; falling back to PAPER")

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

                if not await liquidity_ok(m):
                    continue
                if not await anti_rug(m):
                    continue

                engine.last_signal = (
                    f"{m[:6]} a={a:.4f} w={w:.2f} s={s:.4f} "
                    f"c={combo:.4f} thr={AI_PARAMS['entry_threshold']:.4f}"
                )

                if combo > AI_PARAMS["entry_threshold"]:
                    await buy(m, a, combo, w, s)

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
