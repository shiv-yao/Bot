import httpx

# 🔥 用 Helius API（免費就夠用）
HELIUS = "https://api.helius.xyz/v0"

API_KEY = ""  # 👉 你之後可以放 key（先空也能跑）

async def get_signatures(mint: str):
    try:
        url = f"{HELIUS}/addresses/{mint}/transactions?api-key={API_KEY}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return []

        data = r.json()
        return data[:20]  # 前20筆
    except:
        return []


async def extract_wallets_from_tx(tx_list):
    wallets = set()

    for tx in tx_list:
        try:
            for acc in tx.get("accounts", []):
                if len(acc) > 30:
                    wallets.add(acc)
        except:
            continue

    return list(wallets)


async def real_smart_wallets(RPC, candidates):
    wallets = set()

    for mint in list(candidates)[:10]:  # 限制量
        txs = await get_signatures(mint)
        ws = await extract_wallets_from_tx(txs)

        for w in ws:
            wallets.add(w)

    return list(wallets)[:20]


async def real_smart_signal(RPC, wallets, candidates):
    if not wallets:
        return None

    # 🔥 簡化版：隨機挑一個候選（之後可升級成 wallet tracking）
    for mint in candidates:
        return mint

    return None
