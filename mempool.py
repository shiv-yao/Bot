import json
import websockets

RPC_WS = "wss://api.mainnet-beta.solana.com"

async def mempool_stream(callback):
    async with websockets.connect(RPC_WS) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                "all",
                {"commitment": "processed"}
            ]
        }

        await ws.send(json.dumps(sub))

        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            if "params" in data:
                value = data["params"]["result"]["value"]
                logs = value.get("logs", [])

                for log in logs:
                    if "swap" in log.lower():
                        await callback({
                            "type": "swap",
                            "raw": log,
                        })
                        break
