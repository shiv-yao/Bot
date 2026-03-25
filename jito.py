
import aiohttp, os

JITO = os.getenv("JITO_RPC")

async def send_bundle(session, txs):
    payload = {"jsonrpc":"2.0","id":1,"method":"sendBundle","params":[txs]}
    async with session.post(JITO, json=payload) as r:
        return await r.json()
