import os
import asyncio
import random
import httpx

from alpha_boost_v3 import alpha_fusion

from alpha_boost import (
    wallet_graph_alpha,
    insider_early_alpha,
    smart_flow_alpha,
    momentum_accel_alpha,
)
from paper_engine import PaperEngine
from strategy_state import StrategyState
from smart_wallet_ranker import rank_wallets
from smart_wallet_real import real_smart_wallets, real_smart_signal
from liquidity_engine import liquidity_signal
from smart_wallet_auto_v2 import auto_discover_smart_wallets, smart_wallet_signal_from_auto
from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order
from mempool import mempool_stream
from wallet_graph import wallet_graph_signal
from insider_engine import insider_signal
from alpha_engine import rank_candidates

paper = PaperEngine()
strategy_state = StrategyState()

RPC = os.getenv("RPC", "").strip()
SOL = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "REAL").upper()

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
MIN_POSITION_SOL = float(os.getenv("MIN_POSITION_SOL", "0.001"))
MAX_POSITION_SOL = float(os.getenv("MAX_POSITION_SOL", "0.003"))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", "0.10"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT_PCT", "0.20"))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", "0.08"))
TRAILING = float(os.getenv("TRAILING_STOP_PCT", "0.10"))

ENABLE_AUTO_SELL = os.getenv("ENABLE_AUTO_SELL", "true").lower() == "true"
ADD_ON_WIN = os.getenv("ADD_ON_WIN", "true").lower() == "true"
ADD_TRIGGER_PCT = float(os.getenv("ADD_TRIGGER_PCT", "0.08"))
ADD_SIZE_MULTIPLIER = float(os.getenv("ADD_SIZE_MULTIPLIER", "0.5"))
MAX_ADDS_PER_POSITION = int(os.getenv("MAX_ADDS_PER_POSITION", "1"))

AUTO_SMART_WALLETS = []
LAST_SMART_WALLET_REFRESH = 0.0
REAL_SMART_WALLETS = []
LAST_REAL_REFRESH = 0.0

CANDIDATES = set()


def source_name(alpha_score_value: float) -> str:
    if alpha_score_value >= 1500:
        return "liquidity"
    if alpha_score_value >= 1000:
        return "insider"
    if alpha_score_value >= 900:
        return "real_smart"
    if alpha_score_value >= 700:
        return "auto_smart"
    if alpha_score_value >= 500:
        return "smart_money"
    if alpha_score_value == 35:
        return "early_buy"
    if alpha_score_value == 25:
        return "fast_buy"
    if alpha_score_value == 10:
        return "fallback"
    return f"alpha_{round(alpha_score_value, 2)}"


def strategy_cap_ratio(source: str) -> float:
    caps = {
        "liquidity": 0.30,
        "insider": 0.25,
        "real_smart": 0.25,
        "auto_smart": 0.18,
        "smart_money": 0.18,
        "fast_buy": 0.10,
        "early_buy": 0.08,
        "fallback": 0.05,
    }
    return caps.get(source, 0.10)


def calc_position_size() -> float:
    capital = max(engine.capital, 0.0)
    raw = capital * RISK_PCT_PER_TRADE
    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, raw))


def allocated_exposure_for_source(source: str) -> float:
    total = 0.0
    for p in engine.positions:
        if p.get("source") != source:
            continue

        entry = float(p.get("entry_price", 0.0) or 0.0)
        amount = float(p.get("amount", 0.0) or 0.0)
        if entry > 0 and amount > 0:
            total += entry * amount

    return total


def weighted_position_size(source: str) -> float:
    capital = max(engine.capital, 0.0)
    if capital <= 0:
        return 0.0

    base = capital * RISK_PCT_PER_TRADE
    w = strategy_state.weight(source)

    if w <= 0:
        return 0.0

    size = base * w

    if source == "fallback":
        size *= 0.3

    if source in ["early_buy", "fast_buy"]:
        size *= 0.7

    if w > 1.2:
        size *= 1.2

    size = min(size, MAX_POSITION_SOL)

    cap_ratio = strategy_cap_ratio(source)
    source_cap = capital * cap_ratio
    current_exposure = allocated_exposure_for_source(source)
    remaining = max(0.0, source_cap - current_exposure)

    size = min(size, remaining)

    if size < MIN_POSITION_SOL:
        return 0.0

    available = max(0.0, capital * 0.95)
    size = min(size, available)

    if size < MIN_POSITION_SOL:
        return 0.0

    return size


async def rpc_post(method: str, params: list):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )
        return r.json()
    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"RPC ERROR {e}")
        return None


async def sync_sol_balance():
    if MODE == "PAPER":
        engine.sol_balance = paper.balance
        engine.capital = paper.balance
        return

    kp = load_keypair()
    if not kp:
        engine.log("SAFE MODE: no PRIVATE_KEY")
        return

    res = await rpc_post("getBalance", [str(kp.pubkey())])
    if not res or "result" not in res:
        return

    lamports = res["result"]["value"]
    engine.sol_balance = lamports / 1e9
    engine.capital = engine.sol_balance


async def sync_positions():
    if MODE == "PAPER":
        return

    kp = load_keypair()
    if not kp:
        return

    res = await rpc_post(
        "getTokenAccountsByOwner",
        [
            str(kp.pubkey()),
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    if not res or "result" not in res:
        return

    old_map = {p["token"]: p for p in engine.positions}
    new_positions = []

    for item in res["result"]["value"]:
        info = item["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        amount = float(info["tokenAmount"].get("uiAmount") or 0)

        if amount > 0:
            old = old_map.get(mint, {})
            old_entry = old.get("entry_price", 0.0)
            old_last = old.get("last_price", old_entry)
            old_peak = old.get("peak_price", old_entry)

            new_positions.append({
                "token": mint,
                "amount": amount,
                "entry_price": old_entry if old_entry > 0 else 0.0,
                "last_price": old_last if old_last > 0 else old_entry,
                "peak_price": old_peak if old_peak > 0 else old_entry,
                "pnl_pct": old.get("pnl_pct", 0.0),
                "alpha_score": old.get("alpha_score", 0.0),
                "adds": old.get("adds", 0),
                "source": old.get("source", "unknown"),
            })

    engine.positions = new_positions


async def get_price(mint: str):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

        if r.status_code != 200:
            return None

        data = r.json()
        out_amount = data.get("outAmount")
        if not out_amount:
            return None

        out_sol = int(out_amount) / 1e9
        return out_sol / 1_000_000
    except Exception as e:
        engine.log(f"PRICE ERROR {e}")
        return None


async def send_signed_tx(signed_tx: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        signed_tx,
                        {
                            "skipPreflight": True,
                            "encoding": "base64",
                        },
                    ],
                },
            )

        if r.status_code != 200:
            return None, f"SEND TX FAILED: {r.text}"

        data = r.json()
        if "error" in data:
            return None, f"RPC ERROR: {data['error']}"

        return data, None
    except Exception as e:
        return None, f"SEND TX EXCEPTION: {e}"


async def rug_filter(mint: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "10000000",
                    "slippageBps": 200,
                },
            )

        data = r.json()
        out_amount = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)

        if out_amount == 0:
            return True

        if impact > 0.4:
            return False

        return True
    except Exception:
        return False


def has_position(mint: str) -> bool:
    return any(p["token"] == mint for p in engine.positions)


async def place_buy_order(mint: str, size_sol: float):
    kp = load_keypair()
    if not kp:
        engine.log("NO KEYPAIR")
        return None, None, None

    amount_atomic = int(size_sol * 1e9)

    order = await get_order(
        input_mint=SOL,
        output_mint=mint,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        return None, None, "BUY ORDER FAIL"

    result = await execute_order(order, kp)
    if not result:
        return None, None, "BUY EXEC FAIL"

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        return None, None, "BUY NO SIGNED TX"

    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        return None, None, err

    token_amount = 0.0
    try:
        if "outAmount" in order:
            token_amount = int(order["outAmount"]) / 1_000_000
    except Exception:
        pass

    entry = 0.0
    try:
        if token_amount > 0:
            entry = size_sol / token_amount
    except Exception:
        pass

    if entry <= 0:
        price_now = await get_price(mint)
        if price_now and price_now > 0:
            entry = price_now

    if entry <= 0:
        entry = 1e-9

    return sig_json, token_amount, entry


async def buy(mint: str, alpha_score_value: float = 0.0):
    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("BUY BLOCKED: MAX_POSITIONS")
        return

    if has_position(mint):
        engine.log(f"BUY BLOCKED: ALREADY HAVE {mint[:8]}")
        return

    if not await rug_filter(mint):
        engine.log(f"BUY BLOCKED: RUG FILTER {mint[:8]}")
        return

    src = source_name(alpha_score_value)

    if not strategy_state.enabled(src):
        engine.log(f"STRATEGY DISABLED {src}")
        return

    size = weighted_position_size(src)
    if size <= 0:
        engine.log(f"BUY BLOCKED: ZERO SIZE {src}")
        return

    if MODE == "PAPER":
        price = await get_price(mint)
        if not price:
            return

        paper.buy(
            mint=mint,
            price=price,
            size=size,
            source=src,
        )
        strategy_state.record_buy(src)

        engine.positions.append({
            "token": mint,
            "amount": size / price if price > 0 else 0.0,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl_pct": 0.0,
            "alpha_score": alpha_score_value,
            "adds": 0,
            "source": src,
        })

        engine.stats["buys"] += 1
        engine.last_trade = f"PAPER BUY {mint[:8]}"
        engine.trade_history.append({
            "side": "PAPER_BUY",
            "mint": mint,
            "price": price,
            "size": size,
            "source": src,
            "weight": strategy_state.weight(src),
            "cap_ratio": strategy_cap_ratio(src),
        })
        engine.trade_history = engine.trade_history[-100:]
        engine.log(
            f"🟢 PAPER BUY {mint[:8]} price={price:.12g} src={src} "
            f"w={strategy_state.weight(src):.2f} cap={strategy_cap_ratio(src):.2f} size={size:.6f}"
        )
        return

    if MODE != "REAL":
        engine.log("UNKNOWN MODE: buy skipped")
        return

    engine.log(
        f"TRY BUY {mint[:8]} size={size:.6f} src={src} "
        f"w={strategy_state.weight(src):.2f} cap={strategy_cap_ratio(src):.2f}"
    )

    sig_json, token_amount, entry = await place_buy_order(mint, size)
    if not sig_json:
        engine.stats["errors"] += 1
        engine.log(str(entry) if isinstance(entry, str) else "BUY FAILED")
        return

    strategy_state.record_buy(src)

    engine.positions.append({
        "token": mint,
        "amount": token_amount,
        "entry_price": entry,
        "last_price": entry,
        "peak_price": entry,
        "pnl_pct": 0.0,
        "alpha_score": alpha_score_value,
        "adds": 0,
        "source": src,
    })

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"
    engine.trade_history.append({
        "side": "BUY",
        "mint": mint,
        "result": sig_json,
        "source": src,
        "weight": strategy_state.weight(src),
        "cap_ratio": strategy_cap_ratio(src),
    })
    engine.trade_history = engine.trade_history[-100:]
    engine.log(f"BUY SUCCESS {mint[:8]}")


async def add_to_winner(position: dict):
    mint = position["token"]
    src = position.get("source", "unknown")

    if position.get("adds", 0) >= MAX_ADDS_PER_POSITION:
        return

    add_size = weighted_position_size(src) * ADD_SIZE_MULTIPLIER
    if add_size <= 0:
        return

    if MODE == "PAPER":
        price = await get_price(mint)
        if not price:
            return

        paper.buy(
            mint=mint,
            price=price,
            size=add_size,
            source=src,
        )
        strategy_state.record_buy(src)

        old_amount = position["amount"]
        old_entry = position["entry_price"]
        add_amount = add_size / price if price > 0 else 0.0

        new_amount = old_amount + add_amount
        if new_amount > 0:
            blended_entry = ((old_amount * old_entry) + (add_amount * price)) / new_amount
        else:
            blended_entry = old_entry

        position["amount"] = new_amount
        position["entry_price"] = blended_entry
        position["last_price"] = price
        position["peak_price"] = max(position.get("peak_price", price), price)
        position["adds"] = position.get("adds", 0) + 1

        engine.stats["adds"] += 1
        engine.last_trade = f"PAPER ADD {mint[:8]}"
        engine.log(
            f"🟡 PAPER ADD {mint[:8]} src={src} "
            f"w={strategy_state.weight(src):.2f} size={add_size:.6f}"
        )
        return

    if MODE != "REAL":
        return

    engine.log(f"TRY ADD {mint[:8]} size={add_size:.6f}")

    sig_json, add_token_amount, add_entry = await place_buy_order(mint, add_size)
    if not sig_json:
        engine.stats["errors"] += 1
        engine.log("ADD FAILED")
        return

    strategy_state.record_buy(src)

    old_amount = position["amount"]
    old_entry = position["entry_price"]

    new_amount = old_amount + add_token_amount
    if new_amount > 0:
        blended_entry = ((old_amount * old_entry) + (add_token_amount * add_entry)) / new_amount
    else:
        blended_entry = old_entry

    position["amount"] = new_amount
    position["entry_price"] = blended_entry
    position["last_price"] = blended_entry
    position["peak_price"] = max(position.get("peak_price", blended_entry), blended_entry)
    position["adds"] = position.get("adds", 0) + 1

    engine.stats["adds"] += 1
    engine.last_trade = f"ADD {mint[:8]}"
    engine.trade_history.append({
        "side": "ADD",
        "mint": mint,
        "source": src,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-100:]
    engine.log(f"ADD SUCCESS {mint[:8]}")


async def sell(position: dict):
    src = position.get("source", "unknown")

    if MODE == "PAPER":
        price = await get_price(position["token"])
        if not price:
            return

        pnl, source_from_engine = paper.sell(position["token"], price)
        final_source = source_from_engine or src

        strategy_state.record_sell(final_source, pnl)
        strategy_state.maybe_disable(final_source)

        engine.positions = [
            p for p in engine.positions if p["token"] != position["token"]
        ]

        engine.stats["sells"] += 1
        engine.last_trade = f"PAPER SELL {position['token'][:8]}"
        engine.trade_history.append({
            "side": "PAPER_SELL",
            "mint": position["token"],
            "price": price,
            "pnl": pnl,
            "source": final_source,
        })
        engine.trade_history = engine.trade_history[-100:]
        engine.log(f"🔴 PAPER SELL {position['token'][:8]} PnL={pnl:.6f} src={final_source}")
        return

    if MODE != "REAL":
        engine.log("UNKNOWN MODE: sell skipped")
        return

    kp = load_keypair()
    if not kp:
        engine.log("SELL FAILED: no key")
        return

    mint = position["token"]
    amount_atomic = int(position["amount"] * 1_000_000)

    engine.log(f"TRY SELL {mint[:8]}")

    order = await get_order(
        input_mint=mint,
        output_mint=SOL,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        engine.stats["errors"] += 1
        engine.log("SELL ORDER FAIL")
        return

    result = await execute_order(order, kp)
    if not result:
        engine.stats["errors"] += 1
        engine.log("SELL EXEC FAIL")
        return

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        engine.stats["errors"] += 1
        engine.log("SELL NO SIGNED TX")
        return

    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        engine.stats["errors"] += 1
        engine.log(err)
        return

    pnl = position.get("pnl_pct", 0.0)
    strategy_state.record_sell(src, pnl)
    strategy_state.maybe_disable(src)

    engine.positions = [p for p in engine.positions if p["token"] != mint]
    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {mint[:8]}"
    engine.trade_history.append({
        "side": "SELL",
        "mint": mint,
        "result": sig_json,
        "source": src,
        "pnl_pct": pnl,
    })
    engine.trade_history = engine.trade_history[-100:]
    engine.log(f"SELL SUCCESS {mint[:8]}")


async def monitor():
    while True:
        try:
            if ENABLE_AUTO_SELL:
                for p in list(engine.positions):
                    price = await get_price(p["token"])
                    if not price:
                        continue

                    entry = p.get("entry_price", 0.0)
                    if not entry or entry <= 0:
                        price_now = await get_price(p["token"])
                        if price_now and price_now > 0:
                            p["entry_price"] = price_now
                            p["last_price"] = price_now
                            p["peak_price"] = max(p.get("peak_price", 0.0), price_now)
                            entry = price_now
                            engine.log(f"FIX ENTRY PRICE {p['token'][:8]} {entry}")
                        else:
                            engine.log("SKIP MONITOR: invalid entry_price")
                            continue

                    p["last_price"] = price
                    p["peak_price"] = max(p.get("peak_price", price), price)

                    pnl = (price - entry) / entry
                    p["pnl_pct"] = pnl

                    engine.log(f"{p['token'][:8]} PNL {round(pnl * 100, 2)}%")

                    if ADD_ON_WIN and pnl >= ADD_TRIGGER_PCT and p.get("adds", 0) < MAX_ADDS_PER_POSITION:
                        await add_to_winner(p)

                    if pnl >= TAKE_PROFIT:
                        engine.log("TAKE PROFIT HIT")
                        await sell(p)
                        continue

                    if pnl <= -STOP_LOSS:
                        engine.log("STOP LOSS HIT")
                        await sell(p)
                        continue

                    peak = p.get("peak_price", price)
                    if peak > 0:
                        drawdown = (peak - price) / peak
                        if peak > entry and drawdown >= TRAILING:
                            engine.log("TRAILING STOP HIT")
                            await sell(p)
                            continue

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"MONITOR ERR {e}")

        await asyncio.sleep(5)


async def handle_mempool(event: dict):
    try:
        mint = event.get("mint")
        if not mint:
            return

        if len(mint) < 32 or len(mint) > 44:
            return

        price = await get_price(mint)
        if not price:
            return

        CANDIDATES.add(mint)
        if len(CANDIDATES) > 300:
            CANDIDATES.pop()

        engine.log(f"CANDIDATE ADD {mint[:8]}")
        engine.log(f"CANDIDATES SIZE {len(CANDIDATES)}")

        if len(engine.positions) < MAX_POSITIONS:
            if mint not in [p["token"] for p in engine.positions]:
                if random.random() < 0.4:
                    engine.log(f"EARLY BUY {mint[:8]}")
                    asyncio.create_task(buy(mint, alpha_score_value=35.0))
                else:
                    engine.log(f"FAST BUY {mint[:8]}")
                    asyncio.create_task(buy(mint, alpha_score_value=25.0))

    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"MEMPOOL ERR {e}")


async def bot_loop():
    global AUTO_SMART_WALLETS, LAST_SMART_WALLET_REFRESH
    global REAL_SMART_WALLETS, LAST_REAL_REFRESH

    engine.mode = MODE
    engine.log("🔥 PHASE ALLOCATOR + ALPHA FUSION START")

    asyncio.create_task(monitor())
    asyncio.create_task(mempool_stream(handle_mempool))

    while True:
        try:
            await sync_sol_balance()
            await sync_positions()

            now = asyncio.get_event_loop().time()

            if now - LAST_SMART_WALLET_REFRESH > 5:
                raw_wallets = await auto_discover_smart_wallets(
                    RPC, CANDIDATES, max_wallets=20
                )

                if not raw_wallets:
                    raw_wallets = [f"FALLBACK_{i}" for i in range(5)]

                AUTO_SMART_WALLETS = await rank_wallets(raw_wallets)
                LAST_SMART_WALLET_REFRESH = now

                engine.log(f"SMART RAW {len(raw_wallets)}")
                engine.log(f"SMART RANKED {len(AUTO_SMART_WALLETS)}")

            if now - LAST_REAL_REFRESH > 15:
                REAL_SMART_WALLETS = await real_smart_wallets(RPC, CANDIDATES)

                if not REAL_SMART_WALLETS:
                    REAL_SMART_WALLETS = [f"REAL_FB_{i}" for i in range(3)]

                LAST_REAL_REFRESH = now
                engine.log(f"REAL SMART {len(REAL_SMART_WALLETS)}")

            traded = False

            # ===== ALPHA FUSION（核心） =====

            wg_mint, wg_score = await wallet_graph_alpha(CANDIDATES)
            if wg_mint and not has_position(wg_mint):
                if len(engine.positions) < MAX_POSITIONS:
                    engine.log(f"🧠 WALLET GRAPH {wg_mint[:8]} {wg_score:.2f}")
                    await buy(wg_mint, wg_score)
                    traded = True

            ins_mint, ins_score = await insider_early_alpha(CANDIDATES)
            if ins_mint and not has_position(ins_mint):
                if len(engine.positions) < MAX_POSITIONS:
                    engine.log(f"🧠 INSIDER EARLY {ins_mint[:8]} {ins_score:.2f}")
                    await buy(ins_mint, ins_score)
                    traded = True

            flow_mint, flow_score = await smart_flow_alpha(CANDIDATES)
            if flow_mint and not has_position(flow_mint):
                if len(engine.positions) < MAX_POSITIONS:
                    engine.log(f"🧠 SMART FLOW {flow_mint[:8]} {flow_score:.2f}")
                    await buy(flow_mint, flow_score)
                    traded = True

            mom_mint, mom_score = await momentum_accel_alpha(CANDIDATES)
            if mom_mint and not has_position(mom_mint):
                if len(engine.positions) < MAX_POSITIONS:
                    engine.log(f"🧠 MOMENTUM {mom_mint[:8]} {mom_score:.2f}")
                    await buy(mom_mint, mom_score)
                    traded = True

            # ===== 舊 alpha 層 =====

            liq = await liquidity_signal(RPC)
            if liq and not has_position(liq):
                if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled("liquidity"):
                    engine.log(f"🔥 LIQ {liq[:8]}")
                    await buy(liq, 1500)
                    traded = True

            ins = await insider_signal(RPC)
            if ins and not has_position(ins):
                if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled("insider"):
                    engine.log(f"🔥 INSIDER {ins[:8]}")
                    await buy(ins, 1000)
                    traded = True

            auto_mint = await smart_wallet_signal_from_auto(
                RPC, AUTO_SMART_WALLETS, CANDIDATES
            )
            if auto_mint and not has_position(auto_mint):
                if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled("auto_smart"):
                    engine.log(f"🔥 AUTO SMART {auto_mint[:8]}")
                    await buy(auto_mint, 700)
                    traded = True

            real_mint = await real_smart_signal(
                RPC, REAL_SMART_WALLETS, CANDIDATES
            )
            if real_mint and not has_position(real_mint):
                if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled("real_smart"):
                    engine.log(f"🔥 REAL SMART {real_mint[:8]}")
                    await buy(real_mint, 900)
                    traded = True

            ranked = await rank_candidates(CANDIDATES)
            if ranked:
                best = ranked[0]
                mint = best["mint"]
                score = best["score"]

                rank_src = f"alpha_{round(score, 2)}"
                engine.log(f"BEST {mint[:8]} {score:.2f}")

                if score > 20 and not has_position(mint):
                    if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled(rank_src):
                        engine.log("🔥 RANK BUY")
                        await buy(mint, score)
                        traded = True

            if not traded and CANDIDATES:
                mint = random.choice(list(CANDIDATES))
                if not has_position(mint):
                    if len(engine.positions) < MAX_POSITIONS and strategy_state.enabled("fallback"):
                        engine.log(f"💣 FORCE BUY {mint[:8]}")
                        await buy(mint, 10.0)

            engine.stats["signals"] += 1

            total, by_source = paper.stats()
            engine.log(f"💰 PAPER TOTAL PnL: {total:.9f} SOL")

            for k, v in by_source.items():
                engine.log(
                    f"📊 {k} count={v['count']} wins={v['wins']} losses={v['losses']} "
                    f"total={v['total_pnl']:.9f} avg={v['avg_pnl']:.9f}"
                )

            for name, s in strategy_state.summary().items():
                engine.log(
                    f"🧠 STRAT {name} enabled={s['enabled']} weight={s['weight']:.2f} "
                    f"cap={strategy_cap_ratio(name):.2f} buys={s['buys']} sells={s['sells']} "
                    f"wins={s['wins']} losses={s['losses']} total={s['total_pnl']:.9f} "
                    f"loss_streak={s['loss_streak']}"
                )

            engine.log("LOOP RUNNING")

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP ERROR {e}")

        await asyncio.sleep(4)
