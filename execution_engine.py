class ExecutionEngine:
    def __init__(self, engine):
        self.engine = engine

    async def buy(self, mint, size):
        from bot import buy  # 直接用你原本
        return await buy(mint, size_override=size)

    async def sell(self, position):
        from bot import sell
        return await sell(position)
