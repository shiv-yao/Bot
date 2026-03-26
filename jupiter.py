import base64
import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"


async def get_order(
    input_mint: str,
    output_mint: str,
    amount_atomic: int,
    taker: str,
):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            QUOTE_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_atomic),
                "slippageBps": 100,
            },
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        if "outAmount" not in data:
            return None

        return data


async def execute_order(route, keypair: Keypair):
    async with httpx.AsyncClient(timeout=30) as client:
        swap = await client.post(
            SWAP_URL,
            json={
                "quoteResponse": route,
                "userPublicKey": str(keypair.pubkey()),
                "wrapAndUnwrapSol": True,
            },
        )

        if swap.status_code != 200:
            return None

        swap_data = swap.json()
        tx = swap_data.get("swapTransaction")
        if not tx:
            return None

        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx))
        signed = VersionedTransaction(raw_tx.message, [keypair])

        return {
            "signed_tx": base64.b64encode(bytes(signed)).decode()
        }
