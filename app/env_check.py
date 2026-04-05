import os
from typing import Any, Callable


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _safe_parse(value: str | None, parser: Callable[[str], Any]) -> tuple[bool, Any]:
    if value is None:
        return False, None
    try:
        return True, parser(value)
    except Exception:
        return False, value


ENV_SPECS: dict[str, dict[str, Any]] = {
    "REAL_TRADING": {"type": "bool", "default": "false", "parser": _to_bool},
    "MANUAL_CONFIRM": {"type": "bool", "default": "true", "parser": _to_bool},
    "START_CAPITAL": {"type": "float", "default": "5", "parser": float},
    "MAX_CAPITAL": {"type": "float", "default": "20", "parser": float},
    "MAX_POSITIONS": {"type": "int", "default": "1", "parser": int},
    "MAX_EXPOSURE": {"type": "float", "default": "0.25", "parser": float},
    "MAX_POSITION_SIZE": {"type": "float", "default": "0.05", "parser": float},
    "MIN_ORDER_SOL": {"type": "float", "default": "0.01", "parser": float},
    "LOOP_SLEEP_SEC": {"type": "float", "default": "2", "parser": float},
    "TOKEN_COOLDOWN": {"type": "int", "default": "15", "parser": int},
    "FORCE_TRADE_AFTER": {"type": "int", "default": "20", "parser": int},
    "ENTRY_THRESHOLD": {"type": "float", "default": "0.06", "parser": float},
    "SNIPER_FALLBACK_THRESHOLD": {"type": "float", "default": "0.03", "parser": float},
    "ADAPTIVE_THRESHOLD_MIN": {"type": "float", "default": "0.03", "parser": float},
    "ADAPTIVE_THRESHOLD_MAX": {"type": "float", "default": "0.08", "parser": float},
    "SOFT_DISABLE_FILTER": {"type": "bool", "default": "false", "parser": _to_bool},
    "FILTER_SCORE_BYPASS": {"type": "float", "default": "0.12", "parser": float},
    "TAKE_PROFIT": {"type": "float", "default": "0.08", "parser": float},
    "STOP_LOSS": {"type": "float", "default": "-0.03", "parser": float},
    "TRAILING_GAP": {"type": "float", "default": "0.02", "parser": float},
    "MAX_HOLD_SEC": {"type": "int", "default": "180", "parser": int},
    "MIN_PRICE": {"type": "float", "default": "0.0000000001", "parser": float},
    "MAX_PRICE_JUPITER": {"type": "float", "default": "0.05", "parser": float},
    "MAX_PRICE_FALLBACK": {"type": "float", "default": "5", "parser": float},
    "MIN_OUT_AMOUNT": {"type": "int", "default": "500", "parser": int},
    "MIN_OUT_AMOUNT_STRICT": {"type": "int", "default": "1500", "parser": int},
    "MAX_SCORE": {"type": "float", "default": "1.5", "parser": float},
    "MAX_BREAKOUT_ABS": {"type": "float", "default": "0.10", "parser": float},
    "MAX_PNL_ABS": {"type": "float", "default": "0.30", "parser": float},
    "MIN_UNIVERSE": {"type": "int", "default": "5", "parser": int},
    "BOOT_SYNTHETIC_UNIVERSE": {"type": "bool", "default": "false", "parser": _to_bool},
    "HTTP_TIMEOUT": {"type": "float", "default": "6", "parser": float},
    "ALPHA_BREAKOUT_WEIGHT": {"type": "float", "default": "0.35", "parser": float},
    "ALPHA_MOMENTUM_WEIGHT": {"type": "float", "default": "0.25", "parser": float},
    "ALPHA_SMART_WEIGHT": {"type": "float", "default": "0.25", "parser": float},
    "ALPHA_LIQ_WEIGHT": {"type": "float", "default": "0.10", "parser": float},
    "ALPHA_WALLET_WEIGHT": {"type": "float", "default": "0.05", "parser": float},
    "SNIPER_MULTIPLIER": {"type": "float", "default": "1.3", "parser": float},
    "SMART_MULTIPLIER": {"type": "float", "default": "1.2", "parser": float},
    "MOMENTUM_MULTIPLIER": {"type": "float", "default": "1.0", "parser": float},
    "MAX_CONSEC_LOSS": {"type": "int", "default": "5", "parser": int},
    "DAILY_LOSS_LIMIT": {"type": "float", "default": "-0.15", "parser": float},
    "SOLANA_RPC_HTTPS": {"type": "str", "default": "", "parser": str},
    "SOLANA_RPC_WSS": {"type": "str", "default": "", "parser": str},
    "JUPITER_SLIPPAGE_BPS": {"type": "int", "default": "80", "parser": int},
    "JUPITER_PRIORITY_FEE": {"type": "int", "default": "5000", "parser": int},
    "JITO_ENABLE": {"type": "bool", "default": "false", "parser": _to_bool},
    "JITO_TIP_LAMPORTS": {"type": "int", "default": "0", "parser": int},
    "BIRDEYE_API_KEY": {"type": "str", "default": "", "parser": str},
    "DEBUG_LOG": {"type": "bool", "default": "true", "parser": _to_bool},
}


def _guess_source(key: str, raw_value: str | None, default_value: str) -> str:
    if raw_value is None:
        return "default"
    railway_markers = [
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_SERVICE_ID",
    ]
    if any(os.getenv(m) for m in railway_markers):
        return "railway_or_runtime_env"
    if raw_value == default_value:
        return "env_matches_default"
    return "runtime_env"


def _mask(key: str, value: Any) -> Any:
    secretish = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE")
    if any(word in key for word in secretish):
        s = str(value or "")
        if len(s) <= 6:
            return "***"
        return f"{s[:3]}***{s[-2:]}"
    return value


def _warnings(resolved: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    entry = resolved.get("ENTRY_THRESHOLD")
    if isinstance(entry, (int, float)) and entry > 0.20:
        warnings.append("ENTRY_THRESHOLD 過高，可能導致完全不下單。")

    if resolved.get("SOFT_DISABLE_FILTER") is True:
        warnings.append("SOFT_DISABLE_FILTER=true，可能會放過太多低品質訊號。")

    max_pos = resolved.get("MAX_POSITIONS")
    if isinstance(max_pos, int) and max_pos > 3:
        warnings.append("MAX_POSITIONS 偏高，對早期實測風險較大。")

    max_pos_size = resolved.get("MAX_POSITION_SIZE")
    if isinstance(max_pos_size, (int, float)) and max_pos_size > 0.2:
        warnings.append("MAX_POSITION_SIZE 偏高，單筆風險可能過大。")

    tp = resolved.get("TAKE_PROFIT")
    sl = resolved.get("STOP_LOSS")
    if isinstance(tp, (int, float)) and isinstance(sl, (int, float)):
        if tp <= 0:
            warnings.append("TAKE_PROFIT 應為正數。")
        if sl >= 0:
            warnings.append("STOP_LOSS 應為負數。")

    rpc_https = resolved.get("SOLANA_RPC_HTTPS")
    if not rpc_https:
        warnings.append("SOLANA_RPC_HTTPS 未設定，可能影響報價與交易穩定性。")

    return warnings


def inspect_env() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {}

    for key, spec in ENV_SPECS.items():
        raw = os.getenv(key)
        parser = spec["parser"]
        ok, parsed = _safe_parse(raw, parser)

        if raw is None:
            default_raw = spec["default"]
            default_ok, default_parsed = _safe_parse(default_raw, parser)
            parsed = default_parsed if default_ok else default_raw
            ok = default_ok

        resolved[key] = parsed

        rows.append({
            "key": key,
            "type": spec["type"],
            "raw": _mask(key, raw),
            "effective": _mask(key, parsed),
            "valid": ok,
            "source_guess": _guess_source(key, raw, spec["default"]),
            "default": _mask(key, spec["default"]),
        })

    return {
        "railway_detected": any(
            os.getenv(k) for k in ["RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"]
        ),
        "variables": rows,
        "resolved": {k: _mask(k, v) for k, v in resolved.items()},
        "warnings": _warnings(resolved),
    }
