import time


class PaperEngine:
    def __init__(self):
        self.balance = 10.0
        self.positions = []
        self.trades = []

    def buy(self, mint, price, size, source):
        amount = size / price if price > 0 else 0.0

        self.positions.append({
            "mint": mint,
            "entry": price,
            "amount": amount,
            "source": source,
            "size": size,
            "time": time.time(),
        })

        self.balance -= size

        self.trades.append({
            "side": "BUY",
            "mint": mint,
            "price": price,
            "size": size,
            "source": source,
            "time": time.time(),
        })

    def sell(self, mint, price):
        for p in list(self.positions):
            if p["mint"] == mint:
                value = p["amount"] * price
                cost = p["amount"] * p["entry"]
                pnl = value - cost

                self.balance += value

                self.trades.append({
                    "side": "SELL",
                    "mint": mint,
                    "entry": p["entry"],
                    "exit": price,
                    "pnl": pnl,
                    "source": p["source"],
                    "time": time.time(),
                })

                self.positions.remove(p)
                return pnl, p["source"]

        return 0.0, "unknown"

    def stats(self):
        total = sum(t.get("pnl", 0.0) for t in self.trades if t["side"] == "SELL")

        by_source = {}
        for t in self.trades:
            if t["side"] != "SELL":
                continue

            s = t["source"]
            by_source.setdefault(s, {
                "count": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
            })

            by_source[s]["count"] += 1
            by_source[s]["total_pnl"] += t["pnl"]

            if t["pnl"] > 0:
                by_source[s]["wins"] += 1
            else:
                by_source[s]["losses"] += 1

        for s in by_source:
            c = by_source[s]["count"]
            by_source[s]["avg_pnl"] = by_source[s]["total_pnl"] / c if c > 0 else 0.0

        return total, by_source
