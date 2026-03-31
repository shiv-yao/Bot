def breakout_score(token: dict) -> float:
    """
    趨勢突破分數
    - change 越大越高
    - volume 越大越高
    """
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    vol_score = min(volume / 150000.0, 1.0) * 0.35
    change_score = min(abs(change) / 10.0, 1.0) * 0.65

    # 只偏好多頭
    if change < 0:
        change_score *= 0.4

    return vol_score + change_score
