from core.architecture import build_default_orchestrator


class SignalRouter:
    def __init__(self):
        self.orchestrator = build_default_orchestrator()

    async def get_signal(self, candidates, smart_wallets):
        signals = await self.orchestrator.collect_signals(candidates, smart_wallets)
        if not signals:
            return None

        top = signals[0]
        return top.as_legacy_dict()
