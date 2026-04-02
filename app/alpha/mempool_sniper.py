import asyncio
import websockets
import json

JUP_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5NtH3xWzC"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


async def mempool_stream(callback):
    url = "wss://api.mainnet-beta.solana.com"

    async with websockets.connect(url) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [JUP_PROGRAM, PUMP_PROGRAM]},
                {"commitment": "processed"},
            ],
        }

        await ws.send(json.dumps(sub))

        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            try:
                logs = data["params"]["result"]["value"]["logs"]
                sig = data["params"]["result"]["value"]["signature"]

                # 👉 直接丟給 engine
                await callback(sig, logs)

            except:
                continue
