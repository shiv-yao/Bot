import asyncio


async def scan():
    # 先用穩定 mock，避免外部 API 造成啟動失敗
    await asyncio.sleep(0)
    return [
        {
            "mint": "8F8FLuwv7iL26ecsQ1yXmYKJ6us6Y55QEpJDMFk11Wau",
            "volume": 120000,
            "change": 3.2,
        },
        {
            "mint": "sosd5Q3DutGxMEaukBDmkPgsapMQz59jNjGWmhYcdTQ",
            "volume": 98000,
            "change": 2.7,
        },
        {
            "mint": "SooEj828BSjtgTecBRkqBJ4oquc713yyFZqbCawawoN",
            "volume": 87000,
            "change": 2.2,
        },
        {
            "mint": "sokhCSmzutMPPuNcxG1j6gYLowgiM8mswjJu8FBYm5r",
            "volume": 132000,
            "change": 3.5,
        },
    ]
