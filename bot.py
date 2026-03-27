import os
import asyncio
import random
import time
import httpx

from alpha_boost_v3 import alpha_fusion
from paper_engine import PaperEngine
from strategy_state import StrategyState
from regime import regime_risk_multiplier, regime_take_profit, regime_stop_loss

from smart_wallet_ranker import rank_wallets
from smart_wallet_real import real_smart_wallets
from smart_wallet_auto_v2 import auto_discover_smart_wallets, smart_wallet_signal_from_auto
from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order
from mempool import mempool_stream
from alpha_engine import rank_candidates

paper = PaperEngine()
strategy_state = StrategyState()
strategy_state.disable("fallback")

RPC = os.getenv("RPC", "").strip()
SOL = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "PAPER").upper()

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
MIN_POSITION_SOL = float(os.getenv("MIN_POSITION_SOL", "0.001"))
MAX_POSITION_SOL = float(os.getenv("MAX_POSITION_SOL", "0.0025"))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", "0.08"))

BASE_TAKE_PROFIT = float(os.getenv("TAKE_PROFIT_PCT", "0.12"))
BASE_STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", "0.04"))
TRAILING = float(os.getenv("TRAILING_STOP_PCT", "0.06"))

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


# =========================================================
# V14/V15 Strategy Evolution
# =========================================================
class StrategyEvolution:
    def __init__(self):
        self.performance = {}

    def update(self, strategy: str, pnl: float):
        self.performance.setdefault(strategy, []).append(float(pnl))
        self.performance[strategy] = self.performance[strategy][-40:]

    def weight(self, strategy: str) -> float:
        if not self.performance:
            return 0.05

        scores = {}
        for strat, pnls in self.performance.items():
            if not pnls:
                continue
            avg = sum(pnls) / len(pnls)
            win = sum(1 for x in pnls if x > 0) / len(pnls)
            downside = abs(sum(x for x in pnls if x < 0)) / max(1, len(pnls))
            score = avg * 0.65 + win * 0.30 - downside * 0.10
            scores[strat] = max(score, 0.01)

        total = sum(scores.values()) or 1.0
        return scores.get(strategy, 0.05) / total

    def stats(self):
        return self.performance


evolution = StrategyEvolution()


# =========================================================
# V14 Runtime Allocator
# =========================================================
class AutoAllocator:
    def __init__(self):
        self.global_win = 0
        self.global_loss = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0

    def update(self, pnl: float):
        if pnl > 0:
            self.global_win += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.global_loss += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def size_multiplier(self):
        if self.consecutive_wins >= 4:
            return 1.5
        if self.consecutive_wins >= 2:
            return 1.2
        if self.consecutive_losses >= 4:
            return 0.4
        if self.consecutive_losses >= 2:
            return 0.7
        return 1.0

    def risk_mode(self):
        if self.consecutive_losses >= 4:
            return "hard_defensive"
        if self.consecutive_losses >= 2:
            return "defensive"
        if self.consecutive_wins >= 3:
            return "aggressive"
        return "normal"


allocator_runtime = AutoAllocator()


# =========================================================
# V16 Portfolio Manager
# =========================================================
class PortfolioManager:
    def source_exposure_ratio(self, source: str) -> float:
        capital = max(engine.capital, 1e-9)
        exposure = 0.0
        for p in engine.positions:
            if p.get("source") != source:
                continue
            entry = float(p.get("entry_price", 0.0) or 0.0)
            amount = float(p.get("amount", 0.0) or 0.0)
            if entry > 0 and amount > 0:
                exposure += entry * amount
        return exposure / capital

    def total_exposure_ratio(self) -> float:
        capital = max(engine.capital, 1e-9)
        exposure = 0.0
        for p in engine.positions:
            entry = float(p.get("entry_price", 0.0) or 0.0)
            amount = float(p.get("amount", 0.0) or 0.0)
            if entry > 0 and amount > 0:
                exposure += entry * amount
        return exposure / capital


portfolio = PortfolioManager()


# =========================================================
# V17 Risk Engine
# =========================================================
class RiskEngine:
    def __init__(self):
        self.equity_peak = 0.0
        self.cooldown_until = 0.0
        self.max_drawdown = float(os.getenv("MAX_DRAWDOWN_RATIO", "0.20"))

    def update(self, equity: float):
        self.equity_peak = max(self.equity_peak, equity)

    def drawdown(self, equity: float) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - equity) / self.equity_peak)

    def allow_trade(self, equity: float) -> bool:
        if time.time() < self.cooldown_until:
            return False
        return self.drawdown(equity) < self.max_drawdown

    def trigger_cooldown(self, seconds: int = 120):
        self.cooldown_until = time.time() + seconds


risk_engine = RiskEngine()


# =========================================================
# Helpers
# =========================================================
def source_name(alpha_score_value: float, source_hint: str = None) -> str:
    if source_hint:
        return source_hint
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
    if alpha_score_value == 20:
        return "v19_safe"
    if alpha_score_value == 15:
        return "v19_seed"
    if alpha_score_value == 10:
        return "fallback"
    return f"alpha_{round(alpha_score_value, 2)}"


def strategy_cap_ratio(source: str) -> float:
    caps = {
        "liquidity": 0.20,
        "insider": 0.18,
        "real_smart": 0.18,
        "auto_smart": 0.15,
        "smart_money": 0.15,
        "fusion_liquidity": 0.15,
        "fusion_momentum": 0.12,
        "fusion_volume": 0.10,
        "fusion_anti_rug": 0.10,
        "fast_buy": 0.05,
        "early_buy": 0.04,
        "v19_safe": 0.05,
        "v19_seed": 0.03,
        "fallback": 0.00,
    }
    return caps.get(source, 0.08)


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
    strategy_weight = strategy_state.weight(source)
    evolution_weight = evolution.weight(source)

    if strategy_weight <= 0:
        return 0.0

    size = base * strategy_weight * evolution_weight

    if source in ["early_buy", "fast_buy"]:
        size *= 0.5

    if source in ["v19_safe", "v19_seed"]:
        size *= 0.6

    size = min(size, MAX_POSITION_SOL)

    cap_ratio = strategy_cap_ratio(source)
    source_cap = capital * cap_ratio
    current_exposure = allocated_exposure_for_source(source)
    remaining = max(0.0, source_cap - current_exposure)
    size = min(size, remaining)

    # V16 total portfolio exposure cap
    if portfolio.total_exposure_ratio() > 0.70:
        size *= 0.5

    if size < MIN_POSITION_SOL:
        return 0.0

    return size


def dynamic_size_v19(size: float, strength: float, regime: str, quality: float) -> float:
    if regime == "trend":
        size *= 1.35
    elif regime == "chop":
        size *= 0.60
    elif regime == "trash":
        size *= 0.25

    if strength > 0.05:
        size *= 1.45
    elif strength > 0.02:
        size *= 1.20
    else:
        size *= 0.80

    if quality > 120:
        size *= 1.20
    elif quality < 60:
        size *= 0.60

    return max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))


def score_alpha(strength: float, liq: int, impact: float, smart_hit: bool) -> float:
    liq_score = min(liq / 100000, 3.0) * 25.0
    momentum_score = max(0.0, strength) * 3000.0
    impact_penalty = max(0.0, impact - 0.10) * 120.0
    smart_bonus = 35.0 if smart_hit else 0.0
    return liq_score + momentum_score + smart_bonus - impact_penalty


def classify_alpha(score: float) -> str:
    if score >= 120:
        return "strong"
    if score >= 65:
        return "mid"
    return "weak"


def regime_strategy_gate(regime: str, level: str, source: str) -> bool:
    if regime == "trash":
        return source in ["v19_safe", "v19_seed"]
    if regime == "chop" and level == "weak":
        return False
    return True


async def rpc_post(method: str, params: list):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
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
        risk_engine.update(engine.capital)
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
    risk_engine.update(engine.capital)


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


async def get_liquidity_and_impact(mint: str):
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
        if r.status_code != 200:
            return 0, 1.0
        data = r.json()
        out = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)
        return out, impact
    except Exception:
        return 0, 1.0


async def momentum_confirm(mint: str):
    p1 = await get_price(mint)
    await asyncio.sleep(0.15)
    p2 = await get_price(mint)
    await asyncio.sleep(0.20)
    p3 = await get_price(mint)

    if not p1 or not p2 or not p3 or p1 <= 0 or p2 <= 0:
        return False, 0.0

    m1 = (p2 - p1) / p1
    m2 = (p3 - p2) / p2
    total = (p3 - p1) / p1

    if m1 > 0.003 and total > 0.006:
        return True, max(m1 + m2, total)

    return False, max(total, 0.0)


async def fake_pump_filter(mint: str):
    liq, impact = await get_liquidity_and_impact(mint)
    if impact > 0.40:
        return False
    if liq < 30000:
        return False
    return True


async def liquidity_trap_filter(mint: str):
    liq, impact = await get_liquidity_and_impact(mint)
    if liq < 50000 and impact > 0.18:
        return False, liq, impact
    if liq < 100000 and impact > 0.28:
        return False, liq, impact
    return True, liq, impact


async def smart_money_confirm(mint: str, smart_wallets: list):
    try:
        signal = await smart_wallet_signal_from_auto(RPC, smart_wallets, {mint})
        return signal == mint
    except Exception:
        return False


async def should_enter_v19(mint: str, smart_wallets: list):
    ok_momo, strength = await momentum_confirm(mint)

    ok_fake = await fake_pump_filter(mint)
    if not ok_fake:
        return False, "fake_pump", 0.0, 0, 1.0, False, 0.0, "weak"

    ok_trap, liq, impact = await liquidity_trap_filter(mint)
    if not ok_trap:
        return False, "liquidity_trap", 0.0, liq, impact, False, 0.0, "weak"

    smart_hit = await smart_money_confirm(mint, smart_wallets)
    alpha_score = score_alpha(strength, liq, impact, smart_hit)
    level = classify_alpha(alpha_score)

    if strength < 0.001:
        return False, "too_weak", strength, liq, impact, smart_hit, alpha_score, level

    # V19 salvage: 無 smart hit 也可以用更小倉測試
    if not ok_momo and alpha_score < 55:
        return False, "no_edge", strength, liq, impact, smart_hit, alpha_score, level

    return True, "ok", strength, liq, impact, smart_hit, alpha_score, level


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


async def send_signed_tx(signed_tx: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [signed_tx, {"skipPreflight": True, "encoding": "base64"}],
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
    if token_amount > 0:
        entry = size_sol / token_amount

    if entry <= 0:
        price_now = await get_price(mint)
        if price_now and price_now > 0:
            entry = price_now

    if entry <= 0:
        entry = 1e-9

    return sig_json, token_amount, entry


async def buy(
    mint: str,
    alpha_score_value: float = 0.0,
    source_hint: str = None,
    size_override: float = None,
):
    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("BUY BLOCKED: MAX_POSITIONS")
        return

    if has_position(mint):
        engine.log(f"BUY BLOCKED: ALREADY HAVE {mint[:8]}")
        return

    if not await rug_filter(mint):
        engine.log(f"BUY BLOCKED: RUG FILTER {mint[:8]}")
        return

    src = source_name(alpha_score_value, source_hint)
    if not strategy_state.enabled(src):
        engine.log(f"STRATEGY DISABLED {src}")
        return

    size = size_override if size_override is not None else weighted_position_size(src)
    if size <= 0:
        engine.log(f"BUY BLOCKED: ZERO SIZE {src}")
        return

    if MODE == "PAPER":
        price = await get_price(mint)
        if not price:
            return

        paper.buy(mint=mint, price=price, size=size, source=src)
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
        })
        engine.trade_history = engine.trade_history[-100:]
        engine.log(
            f"🟢 PAPER BUY {mint[:8]} price={price:.12g} src={src} size={size:.6f}"
        )
        return

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

        paper.buy(mint=mint, price=price, size=add_size, source=src)
        strategy_state.record_buy(src)

        old_amount = position["amount"]
        old_entry = position["entry_price"]
        add_amount = add_size / price if price > 0 else 0.0

        new_amount = old_amount + add_amount
        blended_entry = ((old_amount * old_entry) + (add_amount * price)) / new_amount if new_amount > 0 else old_entry

        position["amount"] = new_amount
        position["entry_price"] = blended_entry
        position["last_price"] = price
        position["peak_price"] = max(position.get("peak_price", price), price)
        position["adds"] = position.get("adds", 0) + 1

        engine.stats["adds"] += 1
        engine.last_trade = f"PAPER ADD {mint[:8]}"
        engine.log(f"🟡 PAPER ADD {mint[:8]} src={src} size={add_size:.6f}")
        return


async def sell(position: dict):
    src = position.get("source", "unknown")

    if MODE == "PAPER":
        price = await get_price(position["token"])
        if not price:
            return

        pnl, source_from_engine = paper.sell(position["token"], price)
        final_source = source_from_engine or src

        strategy_state.record_sell(final_source, pnl)
        allocator_runtime.update(pnl)
        evolution.update(final_source, pnl)

        if pnl < 0:
            dd = risk_engine.drawdown(engine.capital)
            if dd > 0.10:
                risk_engine.trigger_cooldown(90)

        engine.positions = [p for p in engine.positions if p["token"] != position["token"]]
        engine.stats["sells"] += 1
        engine.last_trade = f"PAPER SELL {position['token'][:8]}"
        engine.trade_history.append({
            "side": "PAPER_SELL",
            "mint": position["token"],
            "price": price,
            "pnl": pnl,
            "source": final_source,
            "pnl_pct": position.get("pnl_pct", 0.0),
        })
        engine.trade_history = engine.trade_history[-100:]
        engine.log(f"🔴 PAPER SELL {position['token'][:8]} PnL={pnl:.6f} src={final_source}")
        return


async def detect_regime_v19():
    if len(engine.trade_history) < 5:
        return "neutral"

    wins = 0
    total = 0
    avg_abs = 0.0

    for t in engine.trade_history[-12:]:
        pnl = float(t.get("pnl_pct", t.get("pnl", 0)) or 0.0)
        if pnl > 0:
            wins += 1
        avg_abs += abs(pnl)
        total += 1

    if total == 0:
        return "neutral"

    winrate = wins / total
    avg_abs /= total

    if winrate > 0.62 and avg_abs > 0.01:
        return "trend"
    if winrate < 0.38:
        return "chop"
    if avg_abs < 0.003:
        return "trash"
    return "neutral"


async def monitor():
    while True:
        try:
            regime = await detect_regime_v19()
            take_profit = regime_take_profit(regime, BASE_TAKE_PROFIT)
            stop_loss = regime_stop_loss(regime, BASE_STOP_LOSS)

            if ENABLE_AUTO_SELL:
                for p in list(engine.positions):
                    price = await get_price(p["token"])
                    if not price:
                        continue

                    entry = p.get("entry_price", 0.0)
                    if not entry or entry <= 0:
                        continue

                    p["last_price"] = price
                    p["peak_price"] = max(p.get("peak_price", price), price)

                    pnl = (price - entry) / entry
                    p["pnl_pct"] = pnl

                    engine.log(f"{p['token'][:8]} PNL {round(pnl * 100, 2)}%")

                    if ADD_ON_WIN and pnl >= ADD_TRIGGER_PCT and p.get("adds", 0) < MAX_ADDS_PER_POSITION:
                        await add_to_winner(p)

                    if take_profit > 0 and pnl >= take_profit:
                        engine.log("TAKE PROFIT HIT")
                        await sell(p)
                        continue

                    if pnl <= -stop_loss:
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
            try:
                CANDIDATES.pop()
            except Exception:
                pass

        engine.log(f"CANDIDATE ADD {mint[:8]}")
        engine.log(f"CANDIDATES SIZE {len(CANDIDATES)}")
        engine.log(f"CANDIDATE READY {mint[:8]}")

    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"MEMPOOL ERR {e}")


async def bot_loop():
    global AUTO_SMART_WALLETS, LAST_SMART_WALLET_REFRESH
    global REAL_SMART_WALLETS, LAST_REAL_REFRESH

    engine.log("🚨 V19 FULL STACK LOADED")
    engine.log("🛡️ NO FALLBACK MODE")
    engine.mode = MODE

    asyncio.create_task(monitor())
    asyncio.create_task(mempool_stream(handle_mempool))

    while True:
        try:
            await sync_sol_balance()
            await sync_positions()

            risk_engine.update(engine.capital)

            now = asyncio.get_event_loop().time()
            regime = await detect_regime_v19()
            engine.log(f"📊 REGIME {regime}")

            if not risk_engine.allow_trade(engine.capital):
                engine.log("🛑 RISK BLOCK")
                await asyncio.sleep(4)
                continue

            if now - LAST_SMART_WALLET_REFRESH > 5:
                raw_wallets = await auto_discover_smart_wallets(
                    RPC, CANDIDATES, max_wallets=20
                )
                AUTO_SMART_WALLETS = await rank_wallets(raw_wallets or [])
                LAST_SMART_WALLET_REFRESH = now
                engine.log(f"SMART RAW {len(raw_wallets or [])}")
                engine.log(f"SMART RANKED {len(AUTO_SMART_WALLETS)}")

            if now - LAST_REAL_REFRESH > 15:
                REAL_SMART_WALLETS = await real_smart_wallets(RPC, CANDIDATES)
                LAST_REAL_REFRESH = now
                engine.log(f"REAL SMART {len(REAL_SMART_WALLETS)}")

            traded = False

            fusion_mint, fusion_score, fusion_source = await alpha_fusion(CANDIDATES)

            if fusion_mint and not has_position(fusion_mint):
                ok, reason, strength, liq, impact, smart_hit, alpha_score, level = await should_enter_v19(
                    fusion_mint,
                    AUTO_SMART_WALLETS
                )

                if not ok:
                    engine.log(f"❌ FILTERED {fusion_mint[:8]} reason={reason}")
                else:
                    src = fusion_source or "fusion_momentum"

                    if not regime_strategy_gate(regime, level, src):
                        engine.log(f"🚫 GATED {fusion_mint[:8]} regime={regime} level={level}")
                    else:
                        preview_size = weighted_position_size(src)
                        regime_mult = regime_risk_multiplier(regime)
                        base_size = dynamic_size_v19(
                            preview_size * regime_mult,
                            strength,
                            regime,
                            alpha_score,
                        )

                        alloc_mult = allocator_runtime.size_multiplier()
                        portfolio_weight = evolution.weight(src)
                        final_size = base_size * alloc_mult * portfolio_weight

                        mode = allocator_runtime.risk_mode()
                        if mode == "hard_defensive":
                            final_size *= 0.35
                        elif mode == "defensive":
                            final_size *= 0.60
                        elif mode == "aggressive":
                            final_size *= 1.20

                        if level == "strong":
                            final_size *= 1.20
                        elif level == "mid":
                            final_size *= 1.00
                        else:
                            if regime == "chop":
                                final_size = 0.0
                            else:
                                final_size *= 0.45

                        # V16 source exposure control
                        if portfolio.source_exposure_ratio(src) > strategy_cap_ratio(src) * 0.85:
                            final_size *= 0.5

                        final_size = max(0.0, min(final_size, MAX_POSITION_SOL))

                        engine.log(
                            f"📊 SIGNAL {src} score={alpha_score:.2f} "
                            f"level={level} smart={smart_hit} liq={liq} impact={impact:.3f}"
                        )
                        engine.log(f"📊 ALLOC {src} weight={portfolio_weight:.3f}")

                        if final_size >= MIN_POSITION_SOL and len(engine.positions) < MAX_POSITIONS:
                            engine.log(
                                f"🧠 V19 ENTRY {src} {fusion_mint[:8]} "
                                f"{level} strength={strength:.4f} regime={regime} "
                                f"alloc={alloc_mult:.2f} port={portfolio_weight:.3f} "
                                f"size={final_size:.6f}"
                            )
                            await buy(
                                fusion_mint,
                                fusion_score if fusion_score else alpha_score,
                                source_hint=src,
                                size_override=final_size,
                            )
                            traded = True

            if not traded and len(engine.positions) == 0:
                ranked = await rank_candidates(CANDIDATES)
                if ranked:
                    mint = ranked[0]["mint"]
                    engine.log(f"⚡ V19 SAFE ENTRY {mint[:8]}")

                    size = max(MIN_POSITION_SOL * 1.3, 0.0015)
                    size = min(size, MAX_POSITION_SOL)

                    await buy(
                        mint,
                        20.0,
                        source_hint="v19_safe",
                        size_override=size,
                    )
                    traded = True

            if not traded and len(engine.positions) == 0 and CANDIDATES:
                mint = random.choice(list(CANDIDATES))
                if not has_position(mint):
                    size = MIN_POSITION_SOL * 1.2
                    engine.log(f"⚡ V19 SEED ENTRY {mint[:8]}")
                    await buy(
                        mint,
                        15.0,
                        source_hint="v19_seed",
                        size_override=size,
                    )
                    traded = True

            for name, s in strategy_state.summary().items():
                w = evolution.weight(name)
                if s["loss_streak"] >= 3 or w < 0.03:
                    strategy_state.disable(name)
                    engine.log(f"💀 KILLED {name} weight={w:.3f}")

            if not traded:
                engine.log("🚫 NO TRADE (v19 filtered)")

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
                    f"evo={evolution.weight(name):.3f} "
                    f"cap={strategy_cap_ratio(name):.2f} buys={s['buys']} sells={s['sells']} "
                    f"wins={s['wins']} losses={s['losses']} total={s['total_pnl']:.9f}"
                )

            engine.log("LOOP RUNNING")

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP ERROR {e}")

        await asyncio.sleep(4)
