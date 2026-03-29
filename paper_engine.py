import time
import uuid


class PaperEngine:
    def __init__(
        self,
        starting_balance: float = 10.0,
        fee_bps: float = 10.0,
        default_slippage_bps: float = 15.0,
    ):
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)

        # 模擬成本
        self.fee_bps = float(fee_bps)
        self.default_slippage_bps = float(default_slippage_bps)

        # 當前持倉
        # key = mint
        self.positions = {}

        # 已平倉交易
        self.closed_trades = []

        # 權益快照
        self.equity_curve = []

    def _now(self) -> float:
        return time.time()

    def _calc_fee(self, notional: float) -> float:
        return float(notional) * (self.fee_bps / 10000.0)

    def _calc_slippage_cost(self, notional: float, slippage_bps: float = None) -> float:
        bps = self.default_slippage_bps if slippage_bps is None else float(slippage_bps)
        return float(notional) * (bps / 10000.0)

    def buy(
        self,
        mint: str,
        price: float,
        size: float,
        source: str = "unknown",
        slippage_bps: float = None,
    ):
        price = float(price or 0.0)
        size = float(size or 0.0)

        if price <= 0 or size <= 0:
            return False

        fee = self._calc_fee(size)
        slip = self._calc_slippage_cost(size, slippage_bps)
        total_cost = size + fee + slip

        if self.balance < total_cost:
            return False

        effective_price = price * (1.0 + ((self.default_slippage_bps if slippage_bps is None else slippage_bps) / 10000.0))
        amount = size / effective_price if effective_price > 0 else 0.0

        if amount <= 0:
            return False

        now = self._now()

        if mint in self.positions:
            old = self.positions[mint]
            old_amount = float(old["amount"])
            old_entry = float(old["entry_price"])
            old_cost = float(old.get("cost", old_amount * old_entry))

            new_amount = old_amount + amount
            if new_amount <= 0:
                return False

            blended_entry = ((old_amount * old_entry) + (amount * effective_price)) / new_amount

            old["amount"] = new_amount
            old["entry_price"] = blended_entry
            old["last_price"] = effective_price
            old["peak_price"] = max(float(old.get("peak_price", effective_price)), effective_price)
            old["source"] = source
            old["cost"] = old_cost + total_cost
            old["buy_count"] = int(old.get("buy_count", 1)) + 1
            old["fees_paid"] = float(old.get("fees_paid", 0.0)) + fee
            old["slippage_paid"] = float(old.get("slippage_paid", 0.0)) + slip
            old["updated_at"] = now
        else:
            self.positions[mint] = {
                "trade_id": str(uuid.uuid4()),
                "mint": mint,
                "amount": amount,
                "entry_price": effective_price,
                "last_price": effective_price,
                "peak_price": effective_price,
                "low_price": effective_price,
                "source": source,
                "cost": total_cost,
                "fees_paid": fee,
                "slippage_paid": slip,
                "buy_count": 1,
                "sell_count": 0,
                "opened_at": now,
                "updated_at": now,
                "max_unrealized_pnl": 0.0,
                "min_unrealized_pnl": 0.0,
            }

        self.balance -= total_cost
        self.record_equity_point()
        return True

    def sell(
        self,
        mint: str,
        price: float,
        fraction: float = 1.0,
        slippage_bps: float = None,
    ):
        price = float(price or 0.0)
        fraction = float(fraction or 0.0)

        if price <= 0 or fraction <= 0:
            return 0.0, None

        pos = self.positions.get(mint)
        if not pos:
            return 0.0, None

        amount = float(pos["amount"])
        entry = float(pos["entry_price"])
        cost = float(pos.get("cost", entry * amount))
        source = pos.get("source", "unknown")

        if amount <= 0:
            return 0.0, source

        fraction = max(0.0, min(1.0, fraction))
        sell_amount = amount * fraction

        if sell_amount <= 0:
            return 0.0, source

        effective_price = price * (1.0 - ((self.default_slippage_bps if slippage_bps is None else slippage_bps) / 10000.0))
        gross_proceeds = sell_amount * effective_price

        fee = self._calc_fee(gross_proceeds)
        slip = self._calc_slippage_cost(gross_proceeds, slippage_bps)
        net_proceeds = gross_proceeds - fee - slip

        proportional_cost = cost * fraction
        pnl = net_proceeds - proportional_cost

        self.balance += net_proceeds

        now = self._now()
        holding_seconds = 0.0
        if pos.get("opened_at"):
            holding_seconds = max(0.0, now - float(pos["opened_at"]))

        trade_row = {
            "trade_id": pos.get("trade_id"),
            "mint": mint,
            "entry_price": entry,
            "exit_price": effective_price,
            "raw_exit_price": price,
            "amount": sell_amount,
            "fraction": fraction,
            "cost": proportional_cost,
            "gross_proceeds": gross_proceeds,
            "net_proceeds": net_proceeds,
            "fees_paid": fee,
            "slippage_paid": slip,
            "pnl": pnl,
            "pnl_pct": (pnl / proportional_cost) if proportional_cost > 0 else 0.0,
            "source": source,
            "opened_at": pos.get("opened_at"),
            "closed_at": now,
            "holding_seconds": holding_seconds,
            "peak_price": pos.get("peak_price", entry),
            "low_price": pos.get("low_price", entry),
            "mfe_pct": ((float(pos.get("peak_price", entry)) - entry) / entry) if entry > 0 else 0.0,
            "mae_pct": ((float(pos.get("low_price", entry)) - entry) / entry) if entry > 0 else 0.0,
        }
        self.closed_trades.append(trade_row)

        remaining_amount = amount - sell_amount

        if remaining_amount <= 1e-12:
            del self.positions[mint]
        else:
            pos["amount"] = remaining_amount
            pos["cost"] = max(0.0, cost - proportional_cost)
            pos["last_price"] = effective_price
            pos["sell_count"] = int(pos.get("sell_count", 0)) + 1
            pos["fees_paid"] = float(pos.get("fees_paid", 0.0)) + fee
            pos["slippage_paid"] = float(pos.get("slippage_paid", 0.0)) + slip
            pos["updated_at"] = now

        self.record_equity_point()
        return pnl, source

    def mark_price(self, mint: str, price: float):
        price = float(price or 0.0)
        if price <= 0:
            return

        pos = self.positions.get(mint)
        if not pos:
            return

        pos["last_price"] = price
        pos["peak_price"] = max(float(pos.get("peak_price", price)), price)
        pos["low_price"] = min(float(pos.get("low_price", price)), price)

        entry = float(pos.get("entry_price", 0.0) or 0.0)
        amount = float(pos.get("amount", 0.0) or 0.0)
        cost = float(pos.get("cost", entry * amount))

        if entry > 0 and amount > 0 and cost > 0:
            unreal = (price * amount) - cost
            pos["max_unrealized_pnl"] = max(float(pos.get("max_unrealized_pnl", unreal)), unreal)
            pos["min_unrealized_pnl"] = min(float(pos.get("min_unrealized_pnl", unreal)), unreal)

    def unrealized_pnl(self):
        total = 0.0
        by_source = {}

        for _, pos in self.positions.items():
            last_price = float(pos.get("last_price", 0.0) or 0.0)
            amount = float(pos.get("amount", 0.0) or 0.0)
            cost = float(pos.get("cost", 0.0) or 0.0)
            source = pos.get("source", "unknown")

            if last_price <= 0 or amount <= 0:
                continue

            market_value = last_price * amount
            pnl = market_value - cost
            total += pnl

            if source not in by_source:
                by_source[source] = 0.0
            by_source[source] += pnl

        return total, by_source

    def realized_pnl(self):
        total = 0.0
        by_source = {}

        for t in self.closed_trades:
            pnl = float(t.get("pnl", 0.0) or 0.0)
            source = t.get("source", "unknown")

            total += pnl
            if source not in by_source:
                by_source[source] = 0.0
            by_source[source] += pnl

        return total, by_source

    def stats(self):
        realized_total, _ = self.realized_pnl()

        out = {}
        source_names = set()

        for t in self.closed_trades:
            source_names.add(t.get("source", "unknown"))

        for pos in self.positions.values():
            source_names.add(pos.get("source", "unknown"))

        for s in source_names:
            trades = [t for t in self.closed_trades if t.get("source") == s]
            total_pnl = sum(float(t.get("pnl", 0.0) or 0.0) for t in trades)
            count = len(trades)
            wins = sum(1 for t in trades if float(t.get("pnl", 0.0) or 0.0) > 0)
            losses = sum(1 for t in trades if float(t.get("pnl", 0.0) or 0.0) <= 0)
            avg_pnl = total_pnl / count if count > 0 else 0.0
            avg_hold = (
                sum(float(t.get("holding_seconds", 0.0) or 0.0) for t in trades) / count
                if count > 0 else 0.0
            )
            avg_win = (
                sum(float(t.get("pnl", 0.0) or 0.0) for t in trades if float(t.get("pnl", 0.0) or 0.0) > 0) / max(1, wins)
            )
            avg_loss = (
                sum(float(t.get("pnl", 0.0) or 0.0) for t in trades if float(t.get("pnl", 0.0) or 0.0) <= 0) / max(1, losses)
            )

            out[s] = {
                "count": count,
                "wins": wins,
                "losses": losses,
                "winrate": (wins / count) if count > 0 else 0.0,
                "total_pnl": total_pnl,
                "avg_pnl": avg_pnl,
                "avg_hold_seconds": avg_hold,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
            }

        return realized_total, out

    def equity(self):
        unrealized_total, _ = self.unrealized_pnl()
        return self.balance + unrealized_total

    def record_equity_point(self):
        self.equity_curve.append({
            "ts": self._now(),
            "equity": self.equity(),
            "balance": self.balance,
        })
        self.equity_curve = self.equity_curve[-2000:]

    def snapshot(self):
        realized_total, realized_by_source = self.realized_pnl()
        unrealized_total, unrealized_by_source = self.unrealized_pnl()

        return {
            "starting_balance": self.starting_balance,
            "balance": self.balance,
            "realized_pnl": realized_total,
            "unrealized_pnl": unrealized_total,
            "equity": self.equity(),
            "positions": self.positions,
            "closed_trades": self.closed_trades[-200:],
            "realized_by_source": realized_by_source,
            "unrealized_by_source": unrealized_by_source,
            "equity_curve": self.equity_curve[-500:],
        }
