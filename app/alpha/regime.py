def detect_regime(recent_changes: list[float]) -> str:
    if len(recent_changes) < 8:
        return "unknown"

    avg_abs = sum(abs(x) for x in recent_changes) / len(recent_changes)
    avg_signed = sum(recent_changes) / len(recent_changes)

    if avg_abs < 1.8:
        return "flat"

    if avg_signed > 1.5:
        return "trend_up"

    if avg_signed < -1.5:
        return "trend_down"

    return "volatile"
