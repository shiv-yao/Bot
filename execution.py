import asyncio, base64, time
import httpx
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair

JUP_URL = "https://api.jup.ag/swap/v2"

async def confirm_tx(sig, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        # 這裡可以接 RPC confirm
        await asyncio.sleep(1)
        return True
    return False

async def execute(order, keypair, client: httpx.AsyncClient):
    try:
        raw = base64.b64decode(order["transaction"])
        tx = VersionedTransaction.from_bytes(raw)
        signed = VersionedTransaction(tx.message, [keypair])

        res = await client.post(
            f"{JUP_URL}/execute",
            json={
                "signedTransaction": base64.b64encode(bytes(signed)).decode(),
                "requestId": order.get("requestId")
            }
        )

        data = res.json()
        sig = data.get("signature")
        if not sig:
            return None

        ok = await confirm_tx(sig)
        if not ok:
            return None

        return sig

    except Exception as e:
        return None
