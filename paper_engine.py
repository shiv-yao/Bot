class PaperEngine:
    def __init__(self, starting_balance: float = 10.0):
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)

        # 當前 paper 倉位
        # key = mint
        self.positions = {}

        # 已平倉交易
        self.closed_trades = []

    def buy(self, mint: str, price: float, size: float, source: str = "unknown"):
        price = float(price or 0.0)
        size = float(size or 0.0)

        if price <= 0 or size <= 0:
            return False

        if self.balance < size:
            return False

        amount = size / price

        if mint in self.positions:
            old = self.positions[mint]
            old_amount = float(old["amount"])
            old_entry = float(old["entry_price"])

            new_amount = old_amount + amount
            if new_amount <= 0:
                return False

            blended_entry = ((old_amount * old_entry) + (amount * price)) / new_amount

            old["amount"] = new_amount
            old["entry_price"] = blended_entry
            old["last_price"] = price
            old["peak_price"] = max(float(old.get("peak_price", price)), price)
            old["source"] = source
            old["cost"] = float(old.get("cost", 0.0)) + size
        else:
            self.positions[mint] = {
                "mint": mint,
                "amount": amount,
                "entry_price": price,
                "last_price": price,
                "peak_price": price,
                "source": source,
                "cost": size,
            }

        self.balance -= size
        return True

    def sell(self, mint: str, price: float):
        price = float(price or 0.0)
        if price <= 0:
            return 0.0, None

        pos = self.positions.get(mint)
        if not pos:
            return 0.0, None

        amount = float(pos["amount"])
        entry = float(pos["entry_price"])
        cost = float(pos.get("cost", entry * amount))
        source = pos.get("source", "unknown")

        proceeds = amount * price
        pnl = proceeds - cost

        self.balance += proceeds

        self.closed_trades.append({
            "mint": mint,
            "entry_price": entry,
            "exit_price": price,
            "amount": amount,
            "cost": cost,
            "proceeds": proceeds,
            "pnl": pnl,
            "source": source,
        })

        del self.positions[mint]
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

    def unrealized_pnl(self):
        total = 0.0
        by_source = {}

        for mint, pos in self.positions.items():
            entry = float(pos.get("entry_price", 0.0) or 0.0)
            last_price = float(pos.get("last_price", entry) or entry)
            amount = float(pos.get("amount", 0.0) or 0.0)
            source = pos.get("source", "unknown")

            if entry <= 0 or amount <= 0:
                continue

            pnl = (last_price - entry) * amount
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
        realized_total, realized_by_source = self.realized_pnl()

        out = {}
        source_names = set(realized_by_source.keys())

        for pos in self.positions.values():
            source_names.add(pos.get("source", "unknown"))

        for s in source_names:
            trades = [t for t in self.closed_trades if t.get("source") == s]
            total_pnl = sum(float(t.get("pnl", 0.0) or 0.0) for t in trades)
            count = len(trades)
            wins = sum(1 for t in trades if float(t.get("pnl", 0.0) or 0.0) > 0)
            losses = sum(1 for t in trades if float(t.get("pnl", 0.0) or 0.0) <= 0)
            avg_pnl = total_pnl / count if count > 0 else 0.0

            out[s] = {
                "count": count,
                "wins": wins,
                "losses": losses,
                "total_pnl": total_pnl,
                "avg_pnl": avg_pnl,
            }

        return realized_total, out

    def equity(self):
        unrealized_total, _ = self.unrealized_pnl()
        realized_total, _ = self.realized_pnl()
        return self.starting_balance + realized_total + unrealized_total

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
            "closed_trades": self.closed_trades[-100:],
            "realized_by_source": realized_by_source,
            "unrealized_by_source": unrealized_by_source,
        }
