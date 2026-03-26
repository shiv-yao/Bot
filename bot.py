import asyncio
from wallet import get_rpc, load_keypair
from jupiter import get_quote, get_swap_tx
from state import engine

SOL = "So11111111111111111111111111111111111111112"

async def update_balance():
    kp = load_keypair()
    if not kp:
        return

    rpc = get_rpc()
    res = await rpc.get_balance(kp.pubkey())
    engine.sol_balance = res.value / 1e9

async def buy_token(token_mint):
    kp = load_keypair()
    if not kp:
        engine.logs.append("SAFE MODE: no trade")
        return

    try:
        quote = await get_quote(SOL, token_mint, 10000000)  # 0.01 SOL
        swap = await get_swap_tx(quote, kp.pubkey())

        engine.logs.append("BUY EXECUTED")
        engine.stats["buys"] += 1

    except Exception as e:
        engine.logs.append(f"ERROR: {e}")
        engine.stats["errors"] += 1

async def bot_loop():
    while True:
        await update_balance()

        # demo signal
        if engine.sol_balance > 0:
            engine.last_signal = "demo signal"
            await buy_token("Es9vMFrzaCERmJfrF4k5JkX5xRcbkQCk2BEmc6k6Rdt")

        await asyncio.sleep(10)
