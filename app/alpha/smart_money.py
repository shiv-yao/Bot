import math

def smart_money_score(token: dict) -> float:
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    if volume <= 0:
        return 0.0

    vol_component = min(math.log10(volume + 1) / 6.0, 1.0)
    trend_component = min(max(change, 0.0) / 8.0, 1.0)

    if change > 12:
        trend_component *= 0.5

    return vol_component * 0.55 + trend_component * 0.45
