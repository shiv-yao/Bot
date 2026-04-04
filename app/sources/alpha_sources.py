# ================= app/sources/alpha_sources.py =================

import random
import httpx


SOL = "So11111111111111111111111111111111111111112"


async def http_get_json(url, params=None, headers=None, timeout=6):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def fetch_fusion_candidates():
    try:
        from app.sources.fusion import fetch_candidates
        data = await fetch_candidates()
        if isinstance(data, list):
            out = []
            for x in data:
                m = x.get("mint")
                if not m:
                    continue
                out.append({
                    "mint": m,
                    "source": x.get("source", "fusion"),
                    "meta": x,
                })
            return out
    except Exception:
        pass
    return []


async def fetch_pumpfun_candidates(limit=20):
    url = "https://frontend-api.pump.fun/coins/latest"
    data = await http_get_json(url)

    out = []
    if not isinstance(data, list):
        return out

    for row in data[:limit]:
        mint = row.get("mint")
        if not mint:
            continue

        out.append({
            "mint": mint,
            "source": "pumpfun",
            "meta": {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "created_timestamp": row.get("created_timestamp"),
                "reply_count": row.get("reply_count"),
                "market_cap": row.get("market_cap"),
            }
        })
    return out


async def fetch_jupiter_candidates(limit=50):
    urls = [
        "https://lite-api.jup.ag/tokens/v1/mints/tradable",
        "https://cache.jup.ag/tokens",
    ]

    all_rows = []

    for url in urls:
        data = await http_get_json(url)
        if isinstance(data, list):
            all_rows.extend(data)

    out = []
    random.shuffle(all_rows)

    for row in all_rows[:limit]:
        if isinstance(row, str):
            mint = row
            meta = {}
        else:
            mint = row.get("address") or row.get("mint")
            meta = row

        if not mint or mint == SOL:
            continue

        out.append({
            "mint": mint,
            "source": "jupiter",
            "meta": {
                "symbol": meta.get("symbol"),
                "name": meta.get("name"),
                "decimals": meta.get("decimals"),
            }
        })

    return out


async def fetch_dexscreener_candidates(query="sol", limit=30):
    url = "https://api.dexscreener.com/latest/dex/search/"
    data = await http_get_json(url, params={"q": query})

    out = []
    if not data:
        return out

    pairs = data.get("pairs", [])
    if not isinstance(pairs, list):
        return out

    for row in pairs[:limit]:
        base = row.get("baseToken", {}) or {}
        mint = base.get("address")
        if not mint or mint == SOL:
            continue

        out.append({
            "mint": mint,
            "source": "dexscreener",
            "meta": {
                "symbol": base.get("symbol"),
                "name": base.get("name"),
                "liquidity_usd": (row.get("liquidity", {}) or {}).get("usd"),
                "volume_h24": (row.get("volume", {}) or {}).get("h24"),
                "price_usd": row.get("priceUsd"),
                "pair_address": row.get("pairAddress"),
            }
        })

    return out


def source_quality(source: str) -> float:
    if source == "pumpfun":
        return 1.10
    if source == "dexscreener":
        return 1.05
    if source == "fusion":
        return 1.00
    if source == "jupiter":
        return 0.95
    if source == "synthetic":
        return 0.50
    return 1.00


async def fetch_alpha_candidates():
    """
    V38.5 真 alpha universe
    """
    buckets = await __gather_all()
    merged = []
    for xs in buckets:
        if isinstance(xs, list):
            merged.extend(xs)
    return merged


async def __gather_all():
    import asyncio
    results = await asyncio.gather(
        fetch_fusion_candidates(),
        fetch_pumpfun_candidates(),
        fetch_jupiter_candidates(),
        fetch_dexscreener_candidates("sol"),
        return_exceptions=True,
    )

    cleaned = []
    for r in results:
        if isinstance(r, Exception):
            cleaned.append([])
        else:
            cleaned.append(r)
    return cleaned
