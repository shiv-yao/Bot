import os
import json
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair

RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://rpc.ankr.com/solana",
]

rpc_index = 0

def get_rpc():
    global rpc_index
    url = RPCS[rpc_index % len(RPCS)]
    rpc_index += 1
    return AsyncClient(url)

def load_keypair():
    pk = os.getenv("PRIVATE_KEY")

    if not pk:
        print("⚠️ SAFE MODE（沒有私鑰）")
        return None

    arr = json.loads(pk)
    return Keypair.from_bytes(bytes(arr))
