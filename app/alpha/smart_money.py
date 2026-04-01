import httpx

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"


async def fetch_pairs(mint: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(DEX_API.format(mint))
            if r.status_code != 200:
                return []

            data = r.json()
            pairs = data.get("pairs", [])
            return pairs if isinstance(pairs, list) else []
    except Exception:
        return []


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x or default)
    except Exception:
        return default


def calc_flow_score(pair: dict) -> float:
    """
    短週期買賣筆數不平衡
    """
    try:
        m5 = pair.get("txns", {}).get("m5", {}) or {}
        buys = _safe_float(m5.get("buys", 0))
        sells = _safe_float(m5.get("sells", 0))

        total = buys + sells
        if total <= 0:
            return 0.0

        imbalance = (buys - sells) / total
        return max(min(imbalance, 1.0), -1.0)
    except Exception:
        return 0.0


def calc_volume_score(pair: dict) -> float:
    """
    短週期量能強度
    """
    try:
        vol = _safe_float(pair.get("volume", {}).get("m5", 0))
        return max(min(vol / 50000.0, 1.0), 0.0)
    except Exception:
        return 0.0


def calc_liquidity_score(pair: dict) -> float:
    """
    流動性越高越安全，但只作輔助，不主導
    """
    try:
        liq = _safe_float((pair.get("liquidity") or {}).get("usd", 0))
        return max(min(liq / 150000.0, 1.0), 0.0)
    except Exception:
        return 0.0


def calc_price_momentum_score(pair: dict) -> float:
    """
    短期價格變化，避免追太爛的死幣
    """
    try:
        change_m5 = _safe_float((pair.get("priceChange") or {}).get("m5", 0))
        change_h1 = _safe_float((pair.get("priceChange") or {}).get("h1", 0))

        # 以 10% / 30% 為飽和點
        m5_score = max(min(change_m5 / 10.0, 1.0), -1.0)
        h1_score = max(min(change_h1 / 30.0, 1.0), -1.0)

        score = (m5_score * 0.7) + (h1_score * 0.3)
        return max(min(score, 1.0), -1.0)
    except Exception:
        return 0.0


def calc_age_score(pair: dict) -> float:
    """
    越新越有 alpha，但不要過度加權
    """
    try:
        created_at = pair.get("pairCreatedAt")
        if not created_at:
            return 0.0

        # DexScreener 通常是 ms timestamp
        import time
        now_ms = time.time() * 1000.0
        age_minutes = max((now_ms - float(created_at)) / 1000.0 / 60.0, 0.0)

        if age_minutes <= 10:
            return 1.0
        if age_minutes <= 30:
            return 0.8
        if age_minutes <= 120:
            return 0.5
        if age_minutes <= 360:
            return 0.2
        return 0.0
    except Exception:
        return 0.0


def pair_smart_money_score(pair: dict) -> float:
    """
    真 smart money 分數：
    - flow imbalance
    - volume
    - liquidity
    - short momentum
    - freshness
    """
    flow = calc_flow_score(pair)
    volume = calc_volume_score(pair)
    liquidity = calc_liquidity_score(pair)
    momentum = calc_price_momentum_score(pair)
    age = calc_age_score(pair)

    # 主因：flow + momentum
    score = (
        max(flow, 0.0) * 0.35
        + volume * 0.20
        + liquidity * 0.10
        + max(momentum, 0.0) * 0.25
        + age * 0.10
    )

    return round(max(min(score, 1.0), 0.0), 4)


async def smart_money_score(mint: str) -> float:
    pairs = await fetch_pairs(mint)
    if not pairs:
        return 0.0

    best = 0.0

    for pair in pairs[:5]:
        score = pair_smart_money_score(pair)
        if score > best:
            best = score

    return round(best, 4)
