import os, httpx
async def send_bundle(bundle_payload):
    if os.getenv("ENABLE_JITO", "false").lower() != "true": return False
    url = os.getenv("JITO_BUNDLE_URL", "").strip()
    if not url: return False
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json={"jsonrpc":"2.0","id":1,"method":"sendBundle","params":[bundle_payload]})
    return r.status_code == 200
