import time

class PaperEngine:
    def __init__(self):
        self.balance = 10.0  # 初始 10 SOL（可改）
        self.positions = []
        self.trades = []

    def buy(self, mint, price, size, source):
        amount = size / price if price > 0 else 0

        self.positions.append({
            "mint": mint,
            "entry": price,
            "amount": amount,
            "source": source,
            "time": time.time()
        })

        self.balance -= size

        self.trades.append({
            "side": "BUY",
            "mint": mint,
            "price": price,
            "size": size,
            "source": source
        })

    def sell(self, mint, price):
        for p in self.positions:
            if p["mint"] == mint:
                value = p["amount"] * price
                pnl = value - (p["amount"] * p["entry"])

                self.balance += value

                self.trades.append({
                    "side": "SELL",
                    "mint": mint,
                    "entry": p["entry"],
                    "exit": price,
                    "pnl": pnl,
                    "source": p["source"]
                })

                self.positions.remove(p)
                return pnl

        return 0

    def stats(self):
        total = sum(t.get("pnl", 0) for t in self.trades if t["side"] == "SELL")

        by_source = {}
        for t in self.trades:
            if t["side"] != "SELL":
                continue
            s = t["source"]
            by_source.setdefault(s, 0)
            by_source[s] += t["pnl"]

        return total, by_source
