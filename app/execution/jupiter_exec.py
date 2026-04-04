# ================= V37.5 JUPITER REAL EXECUTION =================

import os
import base64
import httpx

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders import message as solders_message

RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

PRIVATE_KEY = os.getenv("PRIVATE_KEY_B58")

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

# ===== WALLET =====
def load_wallet():
    if not PRIVATE_KEY:
        raise Exception("❌ PRIVATE_KEY_B58 missing")

    return Keypair.from_base58_string(PRIVATE_KEY)

WALLET = load_wallet()

# ================= ORDER =================

async def jupiter_order(input_mint, output_mint, amount):

    url = "https://quote-api.jup.ag/v6/swap"

    payload = {
        "userPublicKey": str(WALLET.pubkey()),
        "wrapAndUnwrapSol": True,
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": 100
    }

    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(url, json=payload)
        return r.json()

# ================= SIGN =================

def sign_tx(swap_tx):

    raw = base64.b64decode(swap_tx)
    tx = VersionedTransaction.from_bytes(raw)

    msg = solders_message.to_bytes_versioned(tx.message)
    sig = WALLET.sign_message(msg)

    tx.signatures = [sig]

    return base64.b64encode(bytes(tx)).decode()

# ================= EXECUTE =================

async def send_tx(signed_tx):

    url = RPC

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [signed_tx, {"encoding": "base64"}]
    }

    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(url, json=payload)
        return r.json()

# ================= MAIN =================

async def execute_swap(input_mint, output_mint, amount):

    if not REAL_TRADING:
        return {"paper": True}

    try:
        order = await jupiter_order(input_mint, output_mint, amount)

        swap_tx = order.get("swapTransaction")
        if not swap_tx:
            return {"error": "NO_TX"}

        signed = sign_tx(swap_tx)

        res = await send_tx(signed)

        return res

    except Exception as e:
        return {"error": str(e)}
