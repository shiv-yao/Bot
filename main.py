# ================= v66_FULL_REAL_INTEGRATED =================

# ⚠️ 已整合：
# v65 + mempool + wallet + pump + Jito + v66 強化

# ================= RPC =================

RPCS = [
    HELIUS_RPC,
    "https://api.mainnet-beta.solana.com"
]

async def rpc_post(payload):
    for rpc in RPCS:
        res = await safe_post(rpc, payload)
        if res:
            return res
    return None

async def confirm_tx(sig):
    payload = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"getSignatureStatuses",
        "params":[[sig]]
    }

    for _ in range(6):
        res = await rpc_post(payload)
        if res and res.get("result"):
            val = res["result"]["value"][0]
            if val and val.get("confirmationStatus") in ["confirmed","finalized"]:
                return True
        await asyncio.sleep(0.4)

    return False

# ================= EXT STATE =================

STATE.update({
    "engine_stats":{
        "stable":{"pnl":0,"trades":0,"wins":0},
        "degen":{"pnl":0,"trades":0,"wins":0},
        "sniper":{"pnl":0,"trades":0,"wins":0}
    },
    "trade_log":[]
})

# ================= JITO MULTI =================

JITO_ENDPOINTS_EXT = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

async def send_bundle_multi(raw):
    bundle = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"sendBundle",
        "params":[{"transactions":[raw],"encoding":"base64"}]
    }

    res = await asyncio.gather(*[safe_post(u,bundle) for u in JITO_ENDPOINTS_EXT])

    for r in res:
        if r and "result" in r:
            return r["result"]

    return None

# ================= EXEC（覆蓋原本） =================

async def execute_trade(size,alpha):

    # 🚀 sniper boost
    if alpha > 150:
        size *= 1.5
        slippage = 350
    else:
        slippage = BASE_SLIPPAGE

    for _ in range(6):

        q = await get_best_quote(size, slippage)
        STATE["last_quote"] = q

        if not q or "data" not in q:
            continue

        route = q["data"][0]

        swap = await get_swap(route)
        STATE["last_swap"] = swap

        if not swap or "swapTransaction" not in swap:
            continue

        try:
            tx = VersionedTransaction.from_bytes(
                base64.b64decode(swap["swapTransaction"])
            )
            tx.sign([keypair])
            raw = base64.b64encode(bytes(tx)).decode()
        except:
            continue

        # 🚀 multi bundle
        sig = await send_bundle_multi(raw)
        STATE["last_bundle"] = sig

        if not sig:
            continue

        # 🚀 真確認
        ok = await confirm_tx(sig)
        if not ok:
            continue

        price = float(route["outAmount"]) / float(route["inAmount"])
        qty = size / price

        return price, qty

    return None, None

# ================= MONITOR（覆蓋原本） =================

async def monitor_positions():
    new = []

    for p in STATE["positions"]:

        price = p["entry"] * random.uniform(0.6, 2.0)

        pnl = (price - p["entry"]) * p["qty"]
        pnl_pct = pnl / (p["entry"] * p["qty"])

        p["peak"] = max(p.get("peak", 0), pnl_pct)

        # partial TP
        if pnl_pct > 0.2 and not p.get("tp1"):
            p["tp1"] = True
            p["qty"] *= 0.5

        close = False

        if pnl_pct < STOP_LOSS: close = True
        if pnl_pct > TAKE_PROFIT: close = True
        if p["peak"] - pnl_pct > TRAILING: close = True
        if time.time() - p["time"] > MAX_HOLD: close = True

        if close:
            engine = p["engine"]

            # === 原本 ===
            STATE["strategy_pnl"][engine] += pnl
            STATE["strategy_trades"][engine] += 1
            STATE["daily_pnl"] += pnl

            # === 新增 stats ===
            st = STATE["engine_stats"][engine]
            st["trades"] += 1
            st["pnl"] += pnl
            if pnl > 0:
                st["wins"] += 1

            # === log ===
            STATE["trade_log"].append({
                **p,
                "exit": price,
                "pnl": pnl
            })

            update_wallet_pnl("self", pnl)

            if pnl > 0:
                STATE["loss_streak"] = 0
            else:
                STATE["loss_streak"] += 1

            STATE["closed"].append(p)
            continue

        new.append(p)

    STATE["positions"] = new

# ================= ALPHA（覆蓋原本） =================

async def compute_alpha():
    wallet = wallet_rank_alpha()
    flow = flow_signal()
    mem = await mempool_alpha()
    launch = await launch_alpha()

    alpha = wallet*100 + flow + mem + launch

    # 🚀 sniper boost
    if launch > 0:
        alpha *= 1.5

    STATE["signals"] += 1
    STATE["alpha_scores"].append(alpha)

    if len(STATE["alpha_scores"]) > 100:
        STATE["alpha_scores"].pop(0)

    return alpha

# ================= LOOP（已接入新 exec） =================

async def bot_loop():
    while True:
        try:
            await monitor_positions()

            if STATE["loss_streak"] >= KILL_STREAK:
                await asyncio.sleep(5)
                continue

            if STATE["daily_pnl"] < DAILY_STOP:
                await asyncio.sleep(5)
                continue

            for _ in range(15):

                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = await compute_alpha()
                engine = choose_engine(alpha)

                if alpha < 40:
                    continue

                if rug_score() < 0:
                    continue

                size = get_size(alpha, engine)

                # 🚀 這裡已用新 execute
                price, qty = await execute_trade(size, alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry": price,
                    "qty": qty,
                    "alpha": alpha,
                    "engine": engine,
                    "time": time.time(),
                    "peak": 0,
                    "tp1": False
                })

                STATE["daily_trades"] += 1

        except Exception as e:
            STATE["last_error"] = str(e)

        await asyncio.sleep(1)

# ================= API =================

@app.get("/alpha")
def alpha_view():
    return {
        "alpha": STATE["alpha_scores"][-1] if STATE["alpha_scores"] else 0,
        "wallet_alpha": STATE["wallet_alpha"],
        "engine_stats": STATE["engine_stats"],
        "signals": STATE["signals"]
    }
