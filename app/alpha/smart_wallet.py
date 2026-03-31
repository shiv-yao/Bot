import math


def smart_money_score(token: dict) -> float:
    """
    先用可落地代理特徵，不用假 random。
    用 volume + change 的組合模擬 smart flow：
    - 有量
    - 漲幅不是太誇張
    """
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    if volume <= 0:
        return 0.0

    vol_component = min(math.log10(volume + 1) / 6.0, 1.0)
    trend_component = min(max(change, 0.0) / 8.0, 1.0)

    # 避免超爆拉假 pump
    if change > 12:
        trend_component *= 0.5

    return vol_component * 0.55 + trend_component * 0.45
