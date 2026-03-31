class SignalRouter:
    """
    將不同策略來源統一整理成 routes
    每個 token 只保留最佳來源
    """

    def build_routes(self, tokens: list[dict]) -> list[dict]:
        routes = []

        for t in tokens:
            mint = t.get("mint")
            if not mint:
                continue

            volume = float(t.get("volume", 0))
            change = float(t.get("change", 0))

            # ===== breakout route =====
            breakout_score = min(volume / 150000.0, 1.0) * 0.4 + min(abs(change) / 10.0, 1.0) * 0.6
            routes.append({
                "mint": mint,
                "source": "breakout",
                "score": breakout_score,
                "token": t,
            })

            # ===== smart money route =====
            smart_score = min(volume / 200000.0, 1.0) * 0.5 + min(max(change, 0) / 8.0, 1.0) * 0.5
            routes.append({
                "mint": mint,
                "source": "smart_money",
                "score": smart_score,
                "token": t,
            })

            # ===== liquidity route =====
            liquidity_score = min(volume / 200000.0, 1.0)
            routes.append({
                "mint": mint,
                "source": "liquidity",
                "score": liquidity_score,
                "token": t,
            })

        # ===== 合併：每個 mint 只留最高分 =====
        best = {}

        for r in routes:
            m = r["mint"]
            if m not in best or r["score"] > best[m]["score"]:
                best[m] = r

        return list(best.values())


router = SignalRouter()
