import json
import re
import websockets

RPC_WS = "wss://api.mainnet-beta.solana.com"
SOL_MINT = "So11111111111111111111111111111111111111112"

BASE58_REGEX = r"[1-9A-HJ-NP-Za-km-z]{32,44}"

BAD_WORDS = {
    "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "11111111111111111111111111111111",
}

def looks_like_mint(s: str) -> bool:
    if len(s) < 32 or len(s) > 44:
        return False
    if s in BAD_WORDS:
        return False
    if s == SOL_MINT:
        return False

    # 過濾明顯假字串
    lowered = s.lower()
    for bad in ["compute", "budget", "invoke", "success", "program", "log", "raydium", "jupiter", "system", "token"]:
        if bad in lowered:
            return False

    return True

def extract_mint(logs):
    candidates = []

    for log in logs:
        matches = re.findall(BASE58_REGEX, log)
        for m in matches:
            if looks_like_mint(m):
                candidates.append(m)

    # 去重後回第一個
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            return c

    return None

async def mempool_stream(callback):
    async with websockets.connect(RPC_WS, ping_interval=20, ping_timeout=20) as ws:
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

            # 只處理可能是 swap 的交易
            if not any("swap" in l.lower() or "liquidity" in l.lower() for l in logs):
                continue

            mint = extract_mint(logs)

            if mint:
                await callback({
                    "type": "swap",
                    "mint": mint,
                    "logs": logs,
                })
