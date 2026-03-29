import base64
import httpx
import asyncio
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient

RPC_URL = "https://api.mainnet-beta.solana.com"


async def execute_swap(wallet: Keypair, tx_base64: str):
    try:
        tx_bytes = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        tx.sign([wallet])

        client = AsyncClient(RPC_URL)

        resp = await client.send_raw_transaction(bytes(tx))
        sig = resp.value

        await client.confirm_transaction(sig)

        return sig

    except Exception as e:
        raise RuntimeError(f"EXECUTE ERROR: {e}")
