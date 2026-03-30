import httpx
import base64
import asyncio
from solders.transaction import VersionedTransaction

JUP_BASE = "https://lite-api.jup.ag"

QUOTE_URL = f"{JUP_BASE}/swap/v1/quote"
SWAP_URL = f"{JUP_BASE}/swap/v1/swap"

TIMEOUT = 10


class JupiterClient:
    def __init__(self, rpc_client, keypair, log):
        self.rpc = rpc_client
        self.keypair = keypair
        self.log = log

    async def healthcheck(self):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get("https://lite-api.jup.ag")
                self.log(f"JUP_OK {r.status_code}")
                return True
        except Exception as e:
            self.log(f"JUP_FAIL {type(e).__name__} {e}")
            return False

    async def get_quote(self, input_mint, output_mint, amount, slippage_bps=100):
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
            "onlyDirectRoutes": False,
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(QUOTE_URL, params=params)

                if r.status_code != 200:
                    self.log(f"QUOTE_HTTP_{r.status_code}")
                    return None

                data = r.json()

                if not data or "data" not in data or len(data["data"]) == 0:
                    self.log("NO_ROUTE")
                    return None

                return data["data"][0]

        except Exception as e:
            self.log(f"QUOTE_ERR {type(e).__name__} {e}")
            return None

    async def execute_swap(self, quote, user_pubkey):
        body = {
            "quoteResponse": quote,
            "userPublicKey": str(user_pubkey),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(SWAP_URL, json=body)

                if r.status_code != 200:
                    self.log(f"SWAP_HTTP_{r.status_code}")
                    return None

                data = r.json()
                tx_b64 = data.get("swapTransaction")

                if not tx_b64:
                    self.log("NO_TX_FROM_JUP")
                    return None

                raw_tx = base64.b64decode(tx_b64)
                tx = VersionedTransaction.from_bytes(raw_tx)

                tx.sign([self.keypair])

                sig = await self.rpc.send_raw_transaction(bytes(tx))

                self.log(f"TX_SENT {sig}")

                return sig

        except Exception as e:
            self.log(f"EXEC_ERR {type(e).__name__} {e}")
            return None
