from __future__ import annotations

from collections import Counter
from typing import Any

from .btc5m_ev_metrics import build_ev_metrics
from .btc5m_performance import build_paper_analytics as build_base_paper_analytics


def _data_quality_summary(paper: dict[str, Any]) -> dict[str, Any]:
    skipped = list(paper.get("skipped_rounds") or [])
    invalid = [item for item in skipped if str(item.get("reason") or "").startswith("invalid_btc_")]
    reasons = Counter(str(item.get("reason") or "unknown") for item in invalid)
    return {
        "invalid_rounds": len(invalid),
        "invalid_by_reason": dict(sorted(reasons.items())),
        "skipped_rounds": len(skipped),
        "validity_rate": round(1 - len(invalid) / max(1, len(invalid) + len(paper.get("closed_trades") or [])), 6),
        "note": "Invalid BTC open/close samples are excluded from win rate, PnL and EV analytics.",
    }


def build_paper_analytics(paper_portfolio: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    """Extend existing Paper analytics with V4 quality, EV and Shadow A/B diagnostics.

    The additional diagnostics are observational only. They do not pause the
    strategy, modify thresholds or create additional Paper positions.
    """

    paper = paper_portfolio or {}
    analytics = build_base_paper_analytics(paper, **kwargs)
    analytics["ev"] = build_ev_metrics(paper)
    analytics["shadow_ab"] = dict(paper.get("shadow_ab") or {})
    analytics["data_quality"] = _data_quality_summary(paper)
    return analytics
