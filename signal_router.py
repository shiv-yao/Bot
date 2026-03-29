class SignalRouter:
    def __init__(self):
        pass

    async def get_signal(self, candidates, smart_wallets):
        from alpha_boost_v3 import alpha_fusion

        mint, score, source = await alpha_fusion(candidates)

        if not mint:
            return None

        return {
            "mint": mint,
            "score": score,
            "source": source or "fusion_momentum"
        }
