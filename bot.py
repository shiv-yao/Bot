import asyncio
from wallet import load_keypair, get_rpc
from jupiter import get_quote, get_swap_tx
from state import engine

SOL = "So11111111111111111111111111111111111111112"

async def update_balance():
    kp = load_keypair()
    if not kp:
        engine.logs.append("SAFE MODE")
        return

    rpc = get_rpc()
    res = await rpc.get_balance(kp.pubkey())
    engine.sol_balance = res.value / 1e9

async def buy_once():
    kp = load_keypair()
    if not kp:
        engine.logs.append("NO KEY")
        return

    try:
        engine.logs.append("TRY BUY")

        quote = await get_quote(
            SOL,
            "Es9vMFrzaCERmJfrF4k5JkX5xRcbkQCk2BEmc6k6Rdt",
            10000000  # 0.01 SOL
        )

        swap = await get_swap_tx(quote, kp.pubkey())

        engine.logs.append("BUY SUCCESS")
        engine.last_trade = "BUY USDT"
        engine.stats["buys"] += 1

    except Exception as e:
        engine.logs.append(f"BUY ERROR: {e}")
        engine.stats["errors"] += 1

async def bot_loop():
    while True:
        from wallet import load_keypair

        kp = load_keypair()

        if not kp:
            engine.logs.append("❌ NO PRIVATE KEY")
        else:
            engine.logs.append(f"✅ WALLET: {kp.pubkey()}")

        await asyncio.sleep(5)
