from app.alpha.smart_money import smart_money_score


class SignalRouter:
    """
    多來源訊號路由器
    每個 token 建立多條 route，最後只保留最佳來源
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
            breakout = min(volume / 150000.0, 1.0) * 0.4 + min(abs(change) / 10.0, 1.0) * 0.6
            routes.append({
                "mint": mint,
                "source": "breakout",
                "score": breakout,
                "token": t,
            })

            # ===== smart money route（已改成 wallet-based）=====
            smart_score = smart_money_score(t)
            routes.append({
                "mint": mint,
                "source": "smart_money",
                "score": smart_score,
                "token": t,
            })

            # ===== liquidity route =====
            liquidity = min(volume / 200000.0, 1.0)
            routes.append({
                "mint": mint,
                "source": "liquidity",
                "score": liquidity,
                "token": t,
            })

        # ===== 每個 mint 只保留最高分 route =====
        best = {}

        for r in routes:
            m = r["mint"]
            if m not in best or r["score"] > best[m]["score"]:
                best[m] = r

        # 由高到低排序
        return sorted(best.values(), key=lambda x: x["score"], reverse=True)


router = SignalRouter()
