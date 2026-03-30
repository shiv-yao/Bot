import os, websockets, json
async def stream(cb):
    ws_url = os.getenv("RPC_WS_URL", "wss://api.mainnet-beta.solana.com")
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions":[]},{"commitment":"processed"}]}))
        while True:
            msg = json.loads(await ws.recv())
            logs = msg.get("params", {}).get("result", {}).get("value", {}).get("logs", [])
            for l in logs:
                if len(l) > 30:
                    await cb({"mint": l[-44:]})
