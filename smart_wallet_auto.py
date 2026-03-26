import httpx
from collections import Counter

SOL_MINT = "So11111111111111111111111111111111111111112"

BLACKLIST_WALLETS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
}

async def get_signatures_for_address(rpc: str, address: str, limit: int = 10):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [address, {"limit": limit}],
                },
            )
        data = r.json()
        return data.get("result", [])
    except Exception:
        return []

async def get_transaction(rpc: str, signature: str):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                },
            )
        data = r.json()
        return data.get("result")
    except Exception:
        return None

def extract_wallets_from_tx(tx: dict):
    wallets = []
    try:
        account_keys = tx["transaction"]["message"]["accountKeys"]
        for k in account_keys:
            if isinstance(k, dict):
                pubkey = k.get("pubkey")
            else:
                pubkey = k
            if not pubkey:
                continue
            if pubkey in BLACKLIST_WALLETS:
                continue
            if len(pubkey) < 32 or len(pubkey) > 44:
                continue
            wallets.append(pubkey)
    except Exception:
        pass
    return wallets

def extract_mints_from_tx(tx: dict):
    mints = []
    try:
        meta = tx.get("meta") or {}
        for row in meta.get("postTokenBalances", []) or []:
            mint = row.get("mint")
            if not mint:
                continue
            if mint == SOL_MINT:
                continue
            mints.append(mint)
    except Exception:
        pass
    return mints

async def auto_discover_smart_wallets(rpc: str, candidate_mints: set, max_wallets: int = 10):
    """
    從 candidate mints 反查最近交易，粗略找出常一起出現的 wallet。
    """
    wallet_counter = Counter()

    for mint in list(candidate_mints)[:10]:
        sigs = await get_signatures_for_address(rpc, mint, limit=5)
        for s in sigs:
            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_transaction(rpc, sig)
            if not tx:
                continue

            tx_mints = extract_mints_from_tx(tx)
            if mint not in tx_mints:
                continue

            wallets = extract_wallets_from_tx(tx)
            for w in wallets:
                wallet_counter[w] += 1

    ranked = [w for w, _ in wallet_counter.most_common(max_wallets)]
    return ranked

async def smart_wallet_signal_from_auto(rpc: str, smart_wallets: list):
    """
    從自動抓到的 wallet 裡，再去反查最近交易拿 mint。
    """
    for wallet in smart_wallets:
        sigs = await get_signatures_for_address(rpc, wallet, limit=3)
        for s in sigs:
            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_transaction(rpc, sig)
            if not tx:
                continue

            mints = extract_mints_from_tx(tx)
            for mint in mints:
                if mint != SOL_MINT:
                    return mint
    return None
