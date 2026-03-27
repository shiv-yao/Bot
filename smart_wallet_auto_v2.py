print("SMART_WALLET_AUTO_V2_LOADED")

import httpx
from collections import Counter

SOL_MINT = "So11111111111111111111111111111111111111112"

BLACKLIST_WALLETS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
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

        if r.status_code != 200:
            return []

        data = r.json()
        result = data.get("result", [])
        return result if isinstance(result, list) else []
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

        if r.status_code != 200:
            return None

        data = r.json()
        return data.get("result")
    except Exception:
        return None


def extract_candidate_wallets_from_tx(tx: dict):
    wallets = []

    try:
        account_keys = tx["transaction"]["message"]["accountKeys"]

        for k in account_keys:
            if isinstance(k, dict):
                pubkey = k.get("pubkey")
                signer = k.get("signer", False)
                writable = k.get("writable", False)
            else:
                pubkey = k
                signer = False
                writable = False

            if not pubkey:
                continue
            if pubkey in BLACKLIST_WALLETS:
                continue
            if len(pubkey) < 32 or len(pubkey) > 44:
                continue

            # 優先收 signer / writable
            if signer or writable:
                wallets.append(pubkey)

        # 如果一個都沒抓到，退一步抓前幾個 account key
        if not wallets:
            for k in account_keys[:5]:
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

    dedup = []
    seen = set()
    for w in wallets:
        if w not in seen:
            seen.add(w)
            dedup.append(w)

    return dedup


def extract_mints_from_tx(tx: dict):
    mints = []

    try:
        meta = tx.get("meta") or {}

        for key in ["postTokenBalances", "preTokenBalances"]:
            for row in meta.get(key, []) or []:
                mint = row.get("mint")
                if not mint:
                    continue
                if mint == SOL_MINT:
                    continue
                if len(mint) < 32 or len(mint) > 44:
                    continue
                mints.append(mint)

    except Exception:
        pass

    dedup = []
    seen = set()
    for m in mints:
        if m not in seen:
            seen.add(m)
            dedup.append(m)

    return dedup


async def auto_discover_smart_wallets(rpc: str, candidate_mints: set, max_wallets: int = 10):
    """
    從最新 candidates 的交易中抽常出現 wallet。
    """
    wallet_counter = Counter()

    if not candidate_mints:
        return []

    sample_mints = list(candidate_mints)[-80:]

    for mint in sample_mints:
        sigs = await get_signatures_for_address(rpc, mint, limit=10)

        for s in sigs:
            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_transaction(rpc, sig)
            if not tx:
                continue

            tx_mints = extract_mints_from_tx(tx)
            if not tx_mints:
                continue

            wallets = extract_candidate_wallets_from_tx(tx)
            for w in wallets:
                wallet_counter[w] += 1

    ranked = [w for w, _ in wallet_counter.most_common(max_wallets)]
    return ranked


async def smart_wallet_signal_from_auto(rpc: str, smart_wallets: list, candidates: set):
    """
    只回傳 'smart wallet 最近碰過，而且也在 candidates 裡' 的 mint。
    沒找到就回傳 None，不再亂 fallback。
    """
    if not smart_wallets:
        return None

    if not candidates:
        return None

    for wallet in smart_wallets[:10]:
        sigs = await get_signatures_for_address(rpc, wallet, limit=5)

        for s in sigs:
            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_transaction(rpc, sig)
            if not tx:
                continue

            mints = extract_mints_from_tx(tx)
            for mint in mints:
                if mint != SOL_MINT and mint in candidates:
                    return mint

    return None
