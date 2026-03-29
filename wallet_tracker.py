# ================= wallet_tracker.py =================
import time
from collections import defaultdict

import httpx

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"

IGNORE_MINTS = {SOL, USDC, USDT}

HTTP = httpx.AsyncClient(timeout=12.0, follow_redirects=True)

WALLET_CACHE = {}
LAST_SEEN_SIG = {}
WALLET_TOKEN_SCORE = defaultdict(float)
TOKEN_LAST_SEEN = defaultdict(float)


def now():
    return time.time()


def valid_pubkey(x):
    return isinstance(x, str) and 32 <= len(x) <= 44


def valid_mint(x):
    return valid_pubkey(x) and x not in IGNORE_MINTS


async def rpc_post(rpc_url: str, method: str, params: list):
    try:
        r = await HTTP.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
        )
        if r.status_code != 200:
            return None

        data = r.json()
        if not isinstance(data, dict):
            return None

        return data.get("result")
    except Exception:
        return None


async def get_signatures(rpc_url: str, address: str, limit: int = 10):
    result = await rpc_post(
        rpc_url,
        "getSignaturesForAddress",
        [address, {"limit": limit}],
    )
    return result if isinstance(result, list) else []


async def get_tx(rpc_url: str, sig: str):
    result = await rpc_post(
        rpc_url,
        "getTransaction",
        [
            sig,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    return result if isinstance(result, dict) else None


def extract_candidate_mints(tx: dict):
    out = set()

    meta = tx.get("meta") or {}
    pre_token_balances = meta.get("preTokenBalances") or []
    post_token_balances = meta.get("postTokenBalances") or []

    pre_by_mint = {}
    for row in pre_token_balances:
        if not isinstance(row, dict):
            continue

        mint = row.get("mint")
        ui = row.get("uiTokenAmount") or {}
        amt = ui.get("uiAmount")

        if valid_mint(mint):
            try:
                pre_by_mint[mint] = float(amt or 0.0)
            except Exception:
                pre_by_mint[mint] = 0.0

    for row in post_token_balances:
        if not isinstance(row, dict):
            continue

        mint = row.get("mint")
        ui = row.get("uiTokenAmount") or {}
        amt = ui.get("uiAmount")

        if not valid_mint(mint):
            continue

        try:
            post_amt = float(amt or 0.0)
        except Exception:
            post_amt = 0.0

        pre_amt = pre_by_mint.get(mint, 0.0)

        if post_amt > pre_amt:
            out.add(mint)

    return list(out)


async def poll_wallet_once(rpc_url: str, wallet: str):
    sigs = await get_signatures(rpc_url, wallet, limit=8)
    if not sigs:
        return []

    fresh = []
    seen_sig = LAST_SEEN_SIG.get(wallet)

    for row in sigs:
        if not isinstance(row, dict):
            continue

        sig = row.get("signature")
        if not sig:
            continue

        if sig == seen_sig:
            break

        fresh.append(sig)

    if sigs and isinstance(sigs[0], dict):
        LAST_SEEN_SIG[wallet] = sigs[0].get("signature")

    found = []
    for sig in reversed(fresh):
        tx = await get_tx(rpc_url, sig)
        if not tx:
            continue

        mints = extract_candidate_mints(tx)
        for mint in mints:
            WALLET_TOKEN_SCORE[mint] += 1.0
            TOKEN_LAST_SEEN[mint] = now()
            found.append(mint)

    return found


async def wallet_tracker_loop(rpc_url: str, wallets: list[str], on_token):
    while True:
        try:
            for wallet in wallets:
                if not valid_pubkey(wallet):
                    continue

                found = await poll_wallet_once(rpc_url, wallet)
                for mint in found:
                    await on_token(mint, source="wallet")

                await __sleep(0.25)

        except Exception:
            await __sleep(3)

        await __sleep(4)


def wallet_score(mint: str) -> float:
    base = WALLET_TOKEN_SCORE.get(mint, 0.0)
    age = now() - TOKEN_LAST_SEEN.get(mint, 0.0)

    if age <= 0:
        return base

    decay = max(0.2, 1.0 - min(age / 3600.0, 0.8))
    return base * decay


async def extract_wallets_from_mints(rpc_url: str, mints):
    wallets = set()

    for mint in list(mints)[-20:]:
        if not valid_mint(mint):
            continue

        sigs = await get_signatures(rpc_url, mint, 5)

        for s in sigs:
            if not isinstance(s, dict):
                continue

            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_tx(rpc_url, sig)
            if not tx:
                continue

            try:
                keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
                for k in keys:
                    if isinstance(k, dict):
                        pubkey = k.get("pubkey")
                    else:
                        pubkey = k

                    if valid_pubkey(pubkey):
                        wallets.add(pubkey)
            except Exception:
                continue

        await __sleep(0.15)

    return list(wallets)


async def track_wallet_behavior(rpc_url: str, wallets):
    results = []

    for w in wallets[:20]:
        if not valid_pubkey(w):
            continue

        sigs = await get_signatures(rpc_url, w, 3)
        tokens = set()

        for s in sigs:
            if not isinstance(s, dict):
                continue

            sig = s.get("signature")
            if not sig:
                continue

            tx = await get_tx(rpc_url, sig)
            if not tx:
                continue

            try:
                mints = extract_candidate_mints(tx)
                for mint in mints:
                    if valid_mint(mint):
                        tokens.add(mint)
            except Exception:
                continue

        if tokens:
            item = {
                "wallet": w,
                "tokens": list(tokens),
            }
            results.append(item)
            WALLET_CACHE[w] = {
                "tokens": list(tokens),
                "ts": now(),
            }

        await __sleep(0.15)

    return results


async def discover_active_wallets_from_candidates(rpc_url: str, candidate_mints):
    wallets = await extract_wallets_from_mints(rpc_url, candidate_mints)
    behaviors = await track_wallet_behavior(rpc_url, wallets)

    scored_wallets = []
    for row in behaviors:
        wallet = row.get("wallet")
        tokens = row.get("tokens", [])

        if not valid_pubkey(wallet):
            continue

        score = min(len(tokens), 10)
        scored_wallets.append({
            "wallet": wallet,
            "tokens": tokens,
            "score": score,
        })

    scored_wallets.sort(key=lambda x: x["score"], reverse=True)
    return scored_wallets


async def __sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)
