
import asyncio, aiohttp, os, time, base64
from rpc_pool import RPCPool
from jito import send_bundle
from timing import wait_for_slot
from state import engine, log

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.compute_budget import set_compute_unit_price

rpc_pool = RPCPool()

RPC_ENV = os.getenv("RPC")
PRIVATE_KEY = list(map(int, os.getenv("PRIVATE_KEY").split(",")))
wallet = Keypair.from_bytes(bytes(PRIVATE_KEY))

SOL = "So11111111111111111111111111111111111111112"

flow_cache = {}

def ai(flow, wallets, momentum):
    s = 0
    if flow > 2000: s += 0.4
    if wallets > 5: s += 0.3
    if momentum > flow*0.4: s += 0.3
    return s

async def trade(session, mint):
    try:
        async with session.get("https://quote-api.jup.ag/v6/quote", params={
            "inputMint": SOL,
            "outputMint": mint,
            "amount": int(0.01 * 1e9),
            "slippageBps": 300
        }) as r:
            q = await r.json()

        async with session.post("https://quote-api.jup.ag/v6/swap", json={
            "quoteResponse": q,
            "userPublicKey": str(wallet.pubkey())
        }) as r:
            s = await r.json()

        tx = VersionedTransaction.from_bytes(base64.b64decode(s["swapTransaction"]))
        tx.message.instructions.insert(0, set_compute_unit_price(300000))
        encoded = base64.b64encode(tx.serialize()).decode()
        await send_bundle(session, [encoded])

        log(f"⚡ TRADE {mint}")

    except Exception as e:
        log(f"❌ trade error {e}")

async def bot_loop():
    log("🚀 BOT START")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await rpc_pool.refresh()

                flow = int(time.time()) % 3000
                wallets = flow // 500
                momentum = flow * 0.6

                score = ai(flow, wallets, momentum)

                if score > 0.75:
                    engine["last_signal"] = f"BUY score={score}"
                    await trade(session, SOL)

                await asyncio.sleep(1)

            except Exception as e:
                log(f"⚠️ error {e}")
                await asyncio.sleep(1)
