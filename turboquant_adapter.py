import os
import aiohttp

AI_MODEL_ENDPOINT = os.getenv("AI_MODEL_ENDPOINT")

async def turboquant_score(session: aiohttp.ClientSession, payload: dict):
    if not AI_MODEL_ENDPOINT:
        return None
    try:
        async with session.post(AI_MODEL_ENDPOINT, json=payload, timeout=8) as r:
            if r.status >= 400:
                return None
            data = await r.json()
        score = data.get("score")
        if isinstance(score, (int, float)):
            return float(score)
        return None
    except Exception:
        return None
