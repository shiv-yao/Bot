import httpx

RAYDIUM_PROGRAM = "RVKd61ztZW9Zk5d8Gz9pTn7Y9r8wQfLk2iYzWn1g9w1"


async def get_recent_blocks(rpc: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBlock",
                    "params": [
                        "finalized",
                        {"transactionDetails": "full", "maxSupportedTransactionVersion": 0},
                    ],
                },
            )
        return r.json().get("result", {})
    except:
        return {}


def extract_liquidity_mint(block):
    try:
        txs = block.get("transactions", [])
        for tx in txs:
            message = tx.get("transaction", {}).get("message", {})
            instructions = message.get("instructions", [])

            for ix in instructions:
                program = ix.get("programId")
                if program != RAYDIUM_PROGRAM:
                    continue

                parsed = ix.get("parsed", {})
                info = parsed.get("info", {})
                mint = info.get("mint")

                if mint:
                    return mint
    except:
        pass

    return None


async def liquidity_signal(rpc: str):
    block = await get_recent_blocks(rpc)
    mint = extract_liquidity_mint(block)
    return mint
