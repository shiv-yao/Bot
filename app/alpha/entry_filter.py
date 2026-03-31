def momentum_confirm(momentum: float) -> bool:
    return momentum >= 0.004


def fake_pump_filter(change: float, volume: float) -> bool:
    """
    過濾假拉盤：
    - 漲很多但量不夠
    """
    if change > 12 and volume < 80000:
        return False
    return True


def liquidity_trap_filter(volume: float) -> bool:
    """
    避免低流動性陷阱
    """
    return volume > 30000


def smart_money_confirm(smart_score: float) -> bool:
    return smart_score >= 0.35


def score_alpha(b: float, s: float, l: float) -> float:
    return b * 0.5 + s * 0.3 + l * 0.2


def classify_alpha(score: float) -> str:
    if score >= 0.7:
        return "A+"
    elif score >= 0.6:
        return "A"
    elif score >= 0.5:
        return "B"
    else:
        return "C"


def should_enter(token: dict, meta: dict) -> tuple[bool, str]:
    """
    主入口
    """

    change = float(token.get("change", 0))
    volume = float(token.get("volume", 0))
    momentum = float(meta.get("momentum", 0))
    smart = float(meta.get("smart_money", 0))

    if not momentum_confirm(momentum):
        return False, "momentum_fail"

    if not fake_pump_filter(change, volume):
        return False, "fake_pump"

    if not liquidity_trap_filter(volume):
        return False, "low_liquidity"

    if not smart_money_confirm(smart):
        return False, "no_smart_money"

    return True, "ok"
