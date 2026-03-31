class SignalRouter:
    """
    多來源訊號路由器
    先用同步可得資料建立 route
    真正 smart_money / insider 由 engine.evaluate_route() 再補算
    """

    def build_routes(self, tokens: list[dict]) -> list[dict]:
        routes = []

        for t in tokens:
            mint = t.get("mint")
            if not mint:
                continue

            volume = float(t.get("volume", 0) or 0)
            change = float(t.get("change", 0) or 0)

            # breakout route
            breakout = min(volume / 150000.0, 1.0) * 0.4 + min(abs(change) / 10.0, 1.0) * 0.6
            routes.append({
                "mint": mint,
                "source": "breakout",
                "score": breakout,
                "token": t,
            })

            # liquidity route
            liquidity = min(volume / 200000.0, 1.0)
            routes.append({
                "mint": mint,
                "source": "liquidity",
                "score": liquidity,
                "token": t,
            })

        # 每個 mint 只保留最高分 route
        best = {}
        for r in routes:
            m = r["mint"]
            if m not in best or r["score"] > best[m]["score"]:
                best[m] = r

        return sorted(best.values(), key=lambda x: x["score"], reverse=True)


router = SignalRouter()
