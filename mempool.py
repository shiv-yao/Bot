import json
import re
import websockets

RPC_WS = "wss://api.mainnet-beta.solana.com"
BASE58_REGEX = r"[1-9A-HJ-NP-Za-km-z]{32,44}"


def extract_mint(logs):
    for log in logs:
        matches = re.findall(BASE58_REGEX, log)
        for m in matches:
            if m != "So11111111111111111111111111111111111111112":
                return m
    return None


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

            if "params" not in data:
                continue

            value = data["params"]["result"]["value"]
            logs = value.get("logs", [])

            if not any("swap" in l.lower() or "liquidity" in l.lower() for l in logs):
                continue

            mint = extract_mint(logs)

            if mint:
                await callback({
                    "type": "swap",
                    "mint": mint,
                    "logs": logs,
                })
