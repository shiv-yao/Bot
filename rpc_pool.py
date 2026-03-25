
import time, aiohttp

RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://rpc.ankr.com/solana",
]

class RPCPool:
    def __init__(self):
        self.nodes = {url: 999 for url in RPCS}
        self.current = RPCS[0]

    async def refresh(self):
        async with aiohttp.ClientSession() as session:
            for url in RPCS:
                try:
                    start = time.time()
                    await session.post(url, json={"jsonrpc":"2.0","id":1,"method":"getSlot"}, timeout=2)
                    self.nodes[url] = time.time() - start
                except:
                    self.nodes[url] = 999
        self.current = min(self.nodes, key=self.nodes.get)

    def get(self):
        return self.current
