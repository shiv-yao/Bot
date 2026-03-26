import asyncio
import aiohttp
import os
import time
import base64

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.compute_budget import set_compute_unit_price

# ===== CONFIG =====
CONFIG = {
    "MODE": "PAPER",  # PAPER / REAL
    "MIN_FLOW": 1200,
    "MIN_SMART_SCORE": 0.6,
    "RUG_THRESHOLD": 0.7,
    "PRIORITY_FEE": 150000
}

RPC = os.getenv("RPC")
JITO = os.getenv("JITO_RPC")

if not RPC:
    raise Exception("RPC not set")

WS = RPC.replace("https", "wss")
SOL = "So11111111111111111111111111111111111111112"

# ===== SAFE WALLET INIT =====
pk = os.getenv("PRIVATE_KEY")
wallet = None

if pk = os.getenv("PRIVATE_KEY")
wallet = None

if pk:
    try:
        # 先嘗試逗號數字陣列格式
        if "," in pk:
            private_key = list(map(int, pk.split(",")))
            wallet = Keypair.from_bytes(bytes(private_key))
        else:
            # 再嘗試 base58 格式
            wallet = Keypair.from_base58_string(pk)

        print("✅ Wallet loaded")
    except Exception as e:
        raise Exception(f"PRIVATE_KEY invalid: {e}")
else:
    print("⚠️ PRIVATE_KEY not set; running in SAFE mode (no real trades)")

flow_cache = {}
wallet_stats = {}

# ===== Rug Filter =====
def rug_score(flow, wallets, momentum):
    score = 0
    if wallets < 3:
        score += 0.4
    if momentum < flow * 0.2:
        score += 0.3
    if flow > 2000 and wallets < 5:
        score += 0.3
    return score

# ===== Smart Money =====
def update_wallet(w, pnl):
    s = wallet_stats.setdefault(w, {"pnl": 0, "trades": 0, "wins": 0})
    s["pnl"] += pnl
    s["trades"] += 1
    if pnl > 0:
        s["wins"] += 1

def smart_score(w):
    s = wallet_stats.get(w)
    if not s or s["trades"] < 5:
        return 0
    return s["wins"] / s["trades"]

# ===== AI =====
def ai_score(flow, wallets, momentum):
    score = 0
    if flow > 2000:
        score += 0.4
    if wallets > 6:
        score += 0.3
    if momentum > flow * 0.4:
        score += 0.3
    return score

# ===== TRADE =====
async def trade(session, mint):
    if wallet is None:
        print("⚠️ Skip trade: PRIVATE_KEY not set")
        return False

    if not JITO:
        print("⚠️ Skip trade: JITO_RPC not set")
        return False

    if CONFIG["MODE"] != "REAL":
        print("🧪 PAPER BUY", mint)
        return True

    try:
        async with session.get(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": SOL,
                "outputMint": mint,
                "amount": int(0.01 * 1e9),
                "slippageBps": 300
            }
        ) as r:
            q = await r.json()

        if "error" in q:
            print("quote error:", q)
            return False

        async with session.post(
            "https://quote-api.jup.ag/v6/swap",
            json={
                "quoteResponse": q,
                "userPublicKey": str(wallet.pubkey())
            }
        ) as r:
            s = await r.json()

        if "swapTransaction" not in s:
            print("swap error:", s)
            return False

        tx = VersionedTransaction.from_bytes(base64.b64decode(s["swapTransaction"]))
        tx.message.instructions.insert(
            0,
            set_compute_unit_price(CONFIG["PRIORITY_FEE"])
        )

        encoded = base64.b64encode(tx.serialize()).decode()

        async with session.post(
            JITO,
            json={"transactions": [encoded]}
        ) as r:
            res = await r.json()

        print("⚡ REAL BUY", mint, res)
        return True

    except Exception as e:
        print("trade error:", e)
        return False

# ===== PARSE =====
async def parse_tx(session, sig):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed"}]
    }

    try:
        async with session.post(RPC, json=payload, timeout=10) as r:
            tx = await r.json()

        if not tx.get("result"):
            return None

        meta = tx["result"]["meta"]
        msg = tx["result"]["transaction"]["message"]

        wallet_addr = msg["accountKeys"][0]["pubkey"]

        for b in meta.get("postTokenBalances", []):
            mint = b.get("mint")
            amt = b.get("uiTokenAmount", {}).get("uiAmount")
            if mint and amt is not None and mint != SOL:
                return mint, wallet_addr, float(amt)

    except Exception:
        return None

    return None

# ===== MAIN =====
async def bot_loop():
    print("🚀 PROFIT BOT STARTED")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS) as ws:
            await ws.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": ["all"]
            })

            async for msg in ws:
                try:
                    data = msg.json()
                    if data.get("method") != "logsNotification":
                        continue

                    sig = data["params"]["result"]["value"]["signature"]

                    parsed = await parse_tx(session, sig)
                    if not parsed:
                        continue

                    mint, trader_wallet, amount = parsed

                    bucket = flow_cache.setdefault(mint, [])
                    bucket.append(amount)

                    if len(bucket) < 6:
                        continue

                    flow = sum(bucket[-10:])
                    wallets = len(bucket)
                    momentum = sum(bucket[-3:])

                    rug = rug_score(flow, wallets, momentum)
                    if rug > CONFIG["RUG_THRESHOLD"]:
                        continue

                    sm = smart_score(trader_wallet)
                    if sm < CONFIG["MIN_SMART_SCORE"]:
                        continue

                    ai = ai_score(flow, wallets, momentum)
                    if ai < 0.7:
                        continue

                    print(f"🔥 BUY SIGNAL {mint}")
                    await trade(session, mint)

                except Exception as e:
                    print("loop error:", e)
                    continue
