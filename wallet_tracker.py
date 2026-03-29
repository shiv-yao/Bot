# ================= v1314 WALLET TRACKER (STABLE + COMPATIBLE) =================
import asyncio
import time
from collections import defaultdict

import httpx

HTTP_TIMEOUT = 12.0

# 可忽略的主流 mint
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"

IGNORE_MINTS = {SOL, USDC, USDT}

# 快取
WALLET_CACHE = {}
WALLET_LAST_SEEN = defaultdict(float)
TOKEN_TOUCH_COUNT = defaultdict(int)


def now() -> float:
    return time.time()


def valid_pubkey(x) -> bool:
    return isinstance(x, str) and 32 <= len(x) <= 44


def valid_mint(x) -> bool:
    return valid_pubkey(x) and x not in IGNORE_MINTS


async def rpc_post(RPC: str, method: str, params: list):
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.post(
                RPC,
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


async def get_signatures(RPC: str, address: str, limit: int = 10):
    if not valid_pubkey(address):
        return []

    result = await rpc_post(
        RPC,
        "getSignaturesForAddress",
        [address, {"limit": limit}],
    )

    return result if isinstance(result, list) else []


async def get_tx(RPC: str, sig: str):
    if not isinstance(sig, str) or not sig:
        return None

    result = await rpc_post(
        RPC,
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


def extract_account_keys(tx: dict):
    out = set()

    try:
        keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
        for k in keys:
            if isinstance(k, dict):
                pubkey = k.get("pubkey")
            else:
                pubkey = k

            if valid_pubkey(pubkey):
                out.add(pubkey)
    except Exception:
        return out

    return out


def extract_candidate_mints_from_balances(tx: dict):
    """
    從 token balance 變化抓 mint。
    比直接抓 programId 好，因為 programId 常只是 Token Program / DEX Program。
    """
    out = set()

    try:
        meta = tx.get("meta") or {}
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []

        pre_map = {}
        for row in pre:
            if not isinstance(row, dict):
                continue

            mint = row.get("mint")
            if not valid_mint(mint):
                continue

            ui = row.get("uiTokenAmount") or {}
            amt = ui.get("uiAmount")
            try:
                pre_map[mint] = float(amt or 0.0)
            except Exception:
                pre_map[mint] = 0.0

        for row in post:
            if not isinstance(row, dict):
                continue

            mint = row.get("mint")
            if not valid_mint(mint):
                continue

            ui = row.get("uiTokenAmount") or {}
            amt = ui.get("uiAmount")

            try:
                post_amt = float(amt or 0.0)
            except Exception:
                post_amt = 0.0

            pre_amt = pre_map.get(mint, 0.0)

            # 只要 post > pre，視為這 wallet 最近碰過這顆幣
            if post_amt > pre_amt:
                out.add(mint)

    except Exception:
        return out

    return out


def extract_candidate_mints_from_instructions(tx: dict):
    """
    instruction 裡有時候也會帶 mint / programId / accounts。
    這裡當輔助訊號，不當唯一來源。
    """
    out = set()

    try:
        instructions = ((tx.get("transaction") or {}).get("message") or {}).get("instructions") or []

        for ins in instructions:
            if not isinstance(ins, dict):
                continue

            parsed = ins.get("parsed")
            if isinstance(parsed, dict):
                info = parsed.get("info") or {}
                mint = info.get("mint")
                if valid_mint(mint):
                    out.add(mint)

            accounts = ins.get("accounts") or []
            for acc in accounts:
                if valid_mint(acc):
                    out.add(acc)

    except Exception:
        return out

    return out


def extract_candidate_mints(tx: dict):
    out = set()
    out |= extract_candidate_mints_from_balances(tx)
    out |= extract_candidate_mints_from_instructions(tx)
    return list(out)


async def extract_wallets_from_mints(RPC: str, mints):
    """
    從最近的 mint 反查最近碰過它們的 wallet。
    給 bot build_wallet_graph() 用。
    """
    wallets = set()

    for mint in list(mints)[-20:]:
        if not valid_mint(mint):
            continue

        sigs = await get_signatures(RPC, mint, 5)

        for s in sigs:
            if not isinstance(s, dict):
                continue

            sig = s.get("signature")
            tx = await get_tx(RPC, sig)
            if not tx:
                continue

            keys = extract_account_keys(tx)
            for k in keys:
                wallets.add(k)

        await asyncio.sleep(0.12)

    return list(wallets)


async def track_wallet_behavior(RPC: str, wallets):
    """
    給 bot.py 的 build_wallet_graph() 用。
    回傳:
    [
      {"wallet": "...", "tokens": [...]},
      ...
    ]
    """
    results = []

    for w in wallets[:25]:
        if not valid_pubkey(w):
            continue

        sigs = await get_signatures(RPC, w, 4)
        tokens = set()

        for s in sigs:
            if not isinstance(s, dict):
                continue

            sig = s.get("signature")
            tx = await get_tx(RPC, sig)
            if not tx:
                continue

            mints = extract_candidate_mints(tx)
            for mint in mints:
                if valid_mint(mint):
                    tokens.add(mint)
                    TOKEN_TOUCH_COUNT[mint] += 1
                    WALLET_LAST_SEEN[w] = now()

        if tokens:
            row = {
                "wallet": w,
                "tokens": list(tokens),
            }
            results.append(row)
            WALLET_CACHE[w] = {
                "tokens": list(tokens),
                "ts": now(),
            }

        await asyncio.sleep(0.15)

    return results


# ================= OPTIONAL LIVE TRACKER =================
LAST_SEEN_SIG = {}


async def poll_wallet_once(RPC: str, wallet: str):
    sigs = await get_signatures(RPC, wallet, limit=8)
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
        tx = await get_tx(RPC, sig)
        if not tx:
            continue

        mints = extract_candidate_mints(tx)
        for mint in mints:
            if valid_mint(mint):
                found.append(mint)
                TOKEN_TOUCH_COUNT[mint] += 1

    return found


async def wallet_tracker_loop(RPC: str, wallets: list[str], on_token):
    """
    你 bot 之前有用過這個介面，所以保留。
    on_token 需為 async callback，例如:
      async def on_token(mint): ...
    """
    while True:
        try:
            for wallet in wallets:
                if not valid_pubkey(wallet):
                    continue

                found = await poll_wallet_once(RPC, wallet)
                for mint in found:
                    await on_token(mint)

                await asyncio.sleep(0.25)
        except Exception:
            await asyncio.sleep(3)

        await asyncio.sleep(4)
