from __future__ import annotations

from typing import Any

from .btc5m_ev_metrics import build_ev_metrics
from .btc5m_performance import build_paper_analytics as build_base_paper_analytics


def build_paper_analytics(paper_portfolio: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    """Extend existing Paper analytics with EV and Shadow A/B diagnostics.

    The additional diagnostics are observational only. They do not pause the
    strategy, modify thresholds or create additional Paper positions.
    """

    paper = paper_portfolio or {}
    analytics = build_base_paper_analytics(paper, **kwargs)
    analytics["ev"] = build_ev_metrics(paper)
    analytics["shadow_ab"] = dict(paper.get("shadow_ab") or {})
    return analytics
