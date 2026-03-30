# ================= v1322 FIXED (NO CRASH) =================
import os
import base64
import asyncio
import random
import httpx
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

from state import engine

# ================= CONFIG =================
RPCS = [
    os.getenv("RPC_1", "https://api.mainnet-beta.solana.com"),
    os.getenv("RPC_2", "https://rpc.ankr.com/solana"),
]

PRIVATE_KEY = os.getenv("PRIVATE_KEY")

SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "300"))
BASE_SIZE = float(os.getenv("BASE_SIZE", "0.02"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
TAKE_PROFIT = float(os.getenv("TP", "0.3"))
STOP_LOSS = float(os.getenv("SL", "0.15"))

SOL = "So11111111111111111111111111111111111111112"

# ================= GLOBAL =================
rpc_index = 0
client = AsyncClient(RPCS[rpc_index])

TOKEN_COOLDOWN = defaultdict(float)
last_log = {}

# ================= UTIL =================
def log_once(k, msg, sec=5):
    now = asyncio.get_event_loop().time()
    if now - last_log.get(k, 0) > sec:
        print(msg)
        engine.logs.append(msg)
        last_log[k] = now

def get_client():
    global rpc_index, client
    try:
        return client
    except:
        rpc_index = (rpc_index + 1) % len(RPCS)
        client = AsyncClient(RPCS[rpc_index])
        return client

def keypair():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= JUP =================
async def jup_swap(input_mint, output_mint, amount):
    async with httpx.AsyncClient(timeout=10) as s:
        q = await s.get("https://quote-api.jup.ag/v6/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount * 1e9),
            "slippageBps": SLIPPAGE_BPS,
        })
        data = q.json()

        if not data.get("data"):
            log_once("no_route", f"NO ROUTE {output_mint}")
            return None

        route = data["data"][0]

        res = await s.post("https://quote-api.jup.ag/v6/swap", json={
            "route": route,
            "userPublicKey": str(keypair().pubkey()),
            "wrapAndUnwrapSol": True
        })

        j = res.json()
        return j.get("swapTransaction")

# ================= SEND =================
async def send_tx(tx_base64):
    try:
        kp = keypair()
        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_base64))
        tx.sign([kp])

        client = get_client()
        sig = await client.send_raw_transaction(bytes(tx))

        log_once("send", f"SENT {sig.value}")
        return sig.value

    except Exception as e:
        log_once("send_err", f"ERR {e}")
        return None

# ================= BUY =================
async def buy(token, score):
    if len(engine.positions) >= MAX_POSITIONS:
        return

    if TOKEN_COOLDOWN[token] > asyncio.get_event_loop().time():
        return

    log_once("buy_try", f"BUY {token} s={score:.3f}")

    tx = await jup_swap(SOL, token, BASE_SIZE)

    if not tx:
        log_once("no_tx", f"NO TX {token}")
        return

    sig = await send_tx(tx)

    if sig:
        engine.positions.append({
            "token": token,
            "entry_score": score,
            "size": BASE_SIZE,
            "tx": sig
        })
        engine.stats["buys"] += 1

    TOKEN_COOLDOWN[token] = asyncio.get_event_loop().time() + 20

# ================= SELL =================
async def sell(pos):
    token = pos["token"]

    tx = await jup_swap(token, SOL, pos["size"])
    if not tx:
        return

    sig = await send_tx(tx)

    if sig:
        engine.positions.remove(pos)
        engine.stats["sells"] += 1
        log_once("sell", f"SELL {token}")

# ================= RISK =================
async def risk_loop():
    while True:
        for p in list(engine.positions):
            pnl = random.uniform(-0.3, 0.5)

            if pnl > TAKE_PROFIT or pnl < -STOP_LOSS:
                await sell(p)

        await asyncio.sleep(3)

# ================= ALPHA =================
def score():
    return random.random()

# ================= MAIN =================
async def bot_loop():
    while True:
        try:
            tokens = [
                ("EKpQGSJtjMFqKZ...", score()),  # 改成真 mint
                ("DezXAZ8z7Pnrn...", score()),
            ]

            ranked = sorted(tokens, key=lambda x: x[1], reverse=True)

            for token, s in ranked:
                if s > 0.6:
                    await buy(token, s)

            await asyncio.sleep(2)

        except Exception as e:
            engine.stats["errors"] += 1
            print("ERR", e)
            await asyncio.sleep(2)

# ================= API =================
app = FastAPI()

@app.on_event("startup")
async def start():
    asyncio.create_task(bot_loop())
    asyncio.create_task(risk_loop())

@app.get("/")
async def root():
    return JSONResponse({
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-50:]
    })

# ================= UI =================
@app.get("/ui")
async def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime;font-family:monospace">
    <h2>🔥 SNIPER BOT v1322</h2>
    <div id="data"></div>

    <script>
    async function load(){
        let res = await fetch('/');
        let d = await res.json();
        document.getElementById("data").innerHTML =
            "<pre>"+JSON.stringify(d,null,2)+"</pre>";
    }
    setInterval(load,2000);
    load();
    </script>
    </body>
    </html>
    """)
