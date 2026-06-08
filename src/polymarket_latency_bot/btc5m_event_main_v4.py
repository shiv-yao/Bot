from __future__ import annotations

from typing import Any

from . import btc5m_event_main as legacy
from .btc5m_analytics_v4 import build_paper_analytics as build_v4_paper_analytics
from .btc5m_prediction_market_ui_v4_linked import register_btc5m_prediction_market_ui_v4
from .btc5m_runtime_health import register_btc5m_runtime_health, update_runtime_health
from .btc5m_selfcheck import register_btc5m_selfcheck
from .btc5m_storage_maintenance import register_btc5m_storage_health
from .poly_integrations import register_poly_integrations, update_poly_integrations


legacy.STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V4_HARDENED"
legacy.MODE_NAME = "btc_5m_prediction_market_paper_scale_in_v4_hardened"
legacy.build_paper_analytics = build_v4_paper_analytics
_legacy_build_mode_status = legacy.build_mode_status
_legacy_build_status = legacy.build_status


def register_v4_ui_selfcheck_and_health(app: Any) -> None:
    register_btc5m_prediction_market_ui_v4(app)
    register_btc5m_selfcheck(app)
    register_btc5m_runtime_health(app)
    register_btc5m_storage_health(app)
    register_poly_integrations(app)


legacy.register_btc5m_prediction_market_ui = register_v4_ui_selfcheck_and_health


def build_mode_status() -> dict[str, Any]:
    payload = _legacy_build_mode_status()
    payload["execution"] = "hardened_three_stage_scale_in_50_30_20"
    payload["rules"].update({
        "require_persistent_stage_confirmation": True,
        "require_clean_sources_by_stage": True,
        "require_fusion_for_later_scale_in": True,
        "require_book_imbalance": True,
        "prevent_price_chasing": True,
        "prevent_edge_decay": True,
        "validate_btc_open_close_quality": True,
        "shadow_ab_enabled": True,
        "adaptive_cooldown": False,
        "storage_retention_enabled": True,
        "poly_data_sidecar_supported": True,
        "poly_maker_shadow_enabled": True,
    })
    payload["safety"].update({
        "adaptive_cooldown_enabled": False,
        "poly_data_sidecar_read_only": True,
        "poly_maker_shadow_only": True,
        "poly_maker_live_execution_enabled": False,
    })
    return payload


async def build_status(settings: Any, state: Any) -> dict[str, Any]:
    payload = await _legacy_build_status(settings, state)
    raw_snapshot = await state.snapshot()
    update_runtime_health(payload)
    payload["integrations"] = update_poly_integrations(payload, raw_snapshot)
    return payload


legacy.build_mode_status = build_mode_status
legacy.build_status = build_status


def main() -> None:
    legacy.main()


if __name__ == "__main__":
    main()
