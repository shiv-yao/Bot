import os, httpx
from app.core.state import engine
async def fetch_pump_candidates():
    if os.getenv("PUMP_SOURCE_ENABLED", "true").lower() != "true": return []
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://frontend-api.pump.fun/coins/latest")
    if r.status_code != 200: return []
    data = r.json(); out = []
    if isinstance(data, list):
        for item in data[:20]:
            mint = item.get("mint")
            if mint: out.append({"mint": mint})
    engine.stats["pump_seen"] += len(out)
    return out
