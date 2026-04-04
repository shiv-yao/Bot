import os
import json
import base64
import asyncio
import httpx

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders import message as solders_message

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY_B58", "").strip()
PRIVATE_KEY_JSON = os.getenv("PRIVATE_KEY_JSON", "").strip()
SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com").strip()

JUPITER_QUOTE_URL = os.getenv("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote").strip()
JUPITER_SWAP_URL = os.getenv("JUPITER_SWAP_URL", "https://lite-api.jup.ag/swap/v1/swap").strip()

DEFAULT_SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))
PRIORITY_FEE_LAMPORTS = int(os.getenv("PRIORITY_FEE_LAMPORTS", "5000"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))
MAX_RETRIES = int(os.getenv("EXEC_MAX_RETRIES", "2"))

WALLET = None

def load_wallet():
    if PRIVATE_KEY_B58:
        return Keypair.from_base58_string(PRIVATE_KEY_B58)
    if PRIVATE_KEY_JSON:
        arr = json.loads(PRIVATE_KEY_JSON)
        return Keypair.from_bytes(bytes(arr))
    raise RuntimeError("Missing PRIVATE_KEY_B58 or PRIVATE_KEY_JSON")

def get_wallet():
    global WALLET
    if WALLET is None:
        WALLET = load_wallet()
    return WALLET

async def http_get(url, params=None, timeout=HTTP_TIMEOUT):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

async def http_post(url, payload=None, timeout=HTTP_TIMEOUT):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

async def jupiter_quote(input_mint, output_mint, amount_atomic, slippage_bps=None):
    slippage = DEFAULT_SLIPPAGE_BPS if slippage_bps is None else int(slippage_bps)
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount_atomic)),
        "slippageBps": str(slippage),
        "swapMode": "ExactIn",
    }
    try:
        return await http_get(JUPITER_QUOTE_URL, params=params)
    except Exception as e:
        return {"error": f"QUOTE_FAIL: {e}"}

async def jupiter_swap(quote_response):
    wallet = get_wallet()
    payload = {
        "userPublicKey": str(wallet.pubkey()),
        "quoteResponse": quote_response,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": PRIORITY_FEE_LAMPORTS,
    }
    try:
        return await http_post(JUPITER_SWAP_URL, payload=payload)
    except Exception as e:
        return {"error": f"SWAP_FAIL: {e}"}

def sign_swap_transaction(swap_tx_b64: str) -> str:
    wallet = get_wallet()
    raw_tx = base64.b64decode(swap_tx_b64)
    tx = VersionedTransaction.from_bytes(raw_tx)
    msg_bytes = solders_message.to_bytes_versioned(tx.message)
    sig = wallet.sign_message(msg_bytes)
    signed_tx = VersionedTransaction.populate(tx.message, [sig])
    return base64.b64encode(bytes(signed_tx)).decode()

async def rpc_send_transaction(signed_tx_b64: str):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            signed_tx_b64,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "processed",
                "maxRetries": 3,
            }
        ]
    }
    try:
        return await http_post(SOLANA_RPC, payload=payload)
    except Exception as e:
        return {"error": f"RPC_SEND_FAIL: {e}"}

async def rpc_confirm_signature(signature: str, wait_sec=12):
    deadline = asyncio.get_event_loop().time() + wait_sec
    while asyncio.get_event_loop().time() < deadline:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[signature], {"searchTransactionHistory": True}],
        }
        try:
            data = await http_post(SOLANA_RPC, payload=payload)
            result = data.get("result", {})
            value = result.get("value", [])
            if value and value[0]:
                st = value[0]
                err = st.get("err")
                conf = st.get("confirmationStatus")
                if err is None and conf in ("processed", "confirmed", "finalized"):
                    return {"ok": True, "signature": signature, "status": conf}
                if err is not None:
                    return {"ok": False, "signature": signature, "error": f"CHAIN_ERR: {err}"}
        except Exception:
            pass
        await asyncio.sleep(1)
    return {"ok": False, "signature": signature, "error": "CONFIRM_TIMEOUT"}

async def execute_swap(input_mint, output_mint, amount_atomic):
    try:
        amount_atomic = int(amount_atomic)
    except Exception:
        return {"error": "BAD_AMOUNT"}

    if amount_atomic <= 0:
        return {"error": "BAD_AMOUNT_NONPOSITIVE"}

    if not REAL_TRADING:
        return {
            "paper": True,
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_atomic,
            "quote": {
                "inAmount": str(amount_atomic),
                "outAmount": "0",
                "priceImpactPct": "0",
            },
        }

    try:
        _ = get_wallet()
    except Exception as e:
        return {"error": f"WALLET_INIT_FAIL: {e}"}

    last_err = None

    for _ in range(MAX_RETRIES + 1):
        quote = await jupiter_quote(input_mint, output_mint, amount_atomic)
        if not quote or quote.get("error"):
            last_err = quote.get("error", "NO_QUOTE") if isinstance(quote, dict) else "NO_QUOTE"
            await asyncio.sleep(0.6)
            continue

        if not quote.get("outAmount"):
            last_err = "INVALID_QUOTE"
            await asyncio.sleep(0.6)
            continue

        swap = await jupiter_swap(quote)
        if not swap or swap.get("error"):
            last_err = swap.get("error", "NO_SWAP_TX") if isinstance(swap, dict) else "NO_SWAP_TX"
            await asyncio.sleep(0.6)
            continue

        swap_tx = swap.get("swapTransaction")
        if not swap_tx:
            last_err = "NO_SWAP_TRANSACTION"
            await asyncio.sleep(0.6)
            continue

        try:
            signed_tx = sign_swap_transaction(swap_tx)
        except Exception as e:
            return {"error": f"SIGN_FAIL: {e}"}

        send_res = await rpc_send_transaction(signed_tx)
        if not send_res:
            last_err = "EMPTY_RPC_RESPONSE"
            await asyncio.sleep(0.6)
            continue

        if send_res.get("error"):
            last_err = f"RPC_ERROR: {send_res['error']}"
            await asyncio.sleep(0.6)
            continue

        signature = send_res.get("result")
        if not signature:
            last_err = "NO_SIGNATURE"
            await asyncio.sleep(0.6)
            continue

        conf = await rpc_confirm_signature(signature)
        if conf.get("ok"):
            return {
                "result": signature,
                "confirmed": True,
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_atomic,
                "quote": {
                    "inAmount": quote.get("inAmount"),
                    "outAmount": quote.get("outAmount"),
                    "priceImpactPct": quote.get("priceImpactPct"),
                },
            }

        last_err = conf.get("error", "CONFIRM_FAIL")
        await asyncio.sleep(0.6)

    return {"error": last_err or "EXECUTION_FAILED"}
