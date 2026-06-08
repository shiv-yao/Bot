from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from fastapi.responses import JSONResponse


NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

POLY_DATA_REPOSITORY = "https://github.com/warproxxx/poly_data.git"
POLY_MAKER_REPOSITORY = "https://github.com/warproxxx/poly-maker.git"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _safe_mtime_ms(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _csv_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(next(csv.reader(handle), []))
    except (OSError, StopIteration):
        return []


def _book_depth_usd(levels: list[dict[str, Any]] | None, *, limit: int = 5) -> float:
    total = 0.0
    for level in list(levels or [])[: max(1, int(limit))]:
        try:
            total += float(level.get("price") or 0.0) * float(level.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total, 8)


def _imbalance(bid_depth: float, ask_depth: float) -> float | None:
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return round((bid_depth - ask_depth) / total, 8)


class PolyDataSidecarAdapter:
    """Read output produced by warproxxx/poly_data without copying GPL code.

    The upstream pipeline remains a separate service or process. This adapter
    reads either a remote JSON manifest or local generated files. It never
    imports, mutates or executes upstream code.
    """

    def __init__(self) -> None:
        self.enabled = _env_bool("POLY_DATA_SIDECAR_ENABLED", False)
        self.root = Path(os.getenv("POLY_DATA_SIDECAR_ROOT", "/data/poly_data"))
        self.markets_path = Path(os.getenv("POLY_DATA_MARKETS_PATH", str(self.root / "data" / "markets.csv")))
        self.orders_path = Path(os.getenv("POLY_DATA_ORDERS_PATH", str(self.root / "data" / "orderFilled.csv")))
        self.trades_path = Path(os.getenv("POLY_DATA_TRADES_PATH", str(self.root / "processed" / "trades.csv")))
        self.cursor_path = Path(os.getenv("POLY_DATA_CURSOR_PATH", str(self.root / "data" / "cursor_state.json")))
        self.manifest_url = os.getenv("POLY_DATA_SIDECAR_MANIFEST_URL", "").strip()
        self.manifest_token = os.getenv("POLY_DATA_SIDECAR_MANIFEST_TOKEN", "").strip()
        self.request_timeout_sec = max(1.0, float(os.getenv("POLY_DATA_SIDECAR_TIMEOUT_SEC", "3")))
        self.max_stale_sec = max(60, int(os.getenv("POLY_DATA_MAX_STALE_SEC", "900")))

    def _remote_snapshot(self, *, timestamp_ms: int) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "btc5m-v4-poly-data-adapter"}
        if self.manifest_token:
            headers["Authorization"] = f"Bearer {self.manifest_token}"
        request = Request(self.manifest_url, headers=headers)
        try:
            with urlopen(request, timeout=self.request_timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            manifest = dict(payload) if isinstance(payload, dict) else {}
            manifest_updated_ms = int(manifest.get("updated_at_ms") or 0)
            age_ms = timestamp_ms - manifest_updated_ms if manifest_updated_ms > 0 else None
            stale = age_ms is None or age_ms > self.max_stale_sec * 1000
            upstream_ready = bool(manifest.get("ready", manifest.get("ok", False)))
            status = "stale" if stale else ("ready" if upstream_ready else str(manifest.get("status") or "not_ready"))
            return {
                "enabled": self.enabled,
                "status": status,
                "ready": status == "ready",
                "repository": POLY_DATA_REPOSITORY,
                "integration_mode": "separate_sidecar_remote_manifest_read_only",
                "manifest_url": self.manifest_url,
                "latest_file_age_ms": age_ms,
                "max_stale_sec": self.max_stale_sec,
                "manifest": manifest,
                "license_boundary": "GPL-3.0 upstream runs separately; this adapter reads a standard JSON manifest only.",
                "safety": {
                    "read_only": True,
                    "mutates_upstream_files": False,
                    "places_orders": False,
                    "wallet_signing": False,
                },
            }
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, OSError) as exc:
            return {
                "enabled": self.enabled,
                "status": "manifest_unavailable",
                "ready": False,
                "repository": POLY_DATA_REPOSITORY,
                "integration_mode": "separate_sidecar_remote_manifest_read_only",
                "manifest_url": self.manifest_url,
                "error": f"{type(exc).__name__}: {exc}",
                "license_boundary": "GPL-3.0 upstream runs separately; this adapter reads a standard JSON manifest only.",
                "safety": {
                    "read_only": True,
                    "mutates_upstream_files": False,
                    "places_orders": False,
                    "wallet_signing": False,
                },
            }

    def _local_snapshot(self, *, timestamp_ms: int) -> dict[str, Any]:
        files = {
            "markets": self.markets_path,
            "orders": self.orders_path,
            "trades": self.trades_path,
            "cursor": self.cursor_path,
        }
        file_rows: dict[str, Any] = {}
        newest_mtime_ms: int | None = None
        for name, path in files.items():
            mtime_ms = _safe_mtime_ms(path)
            if mtime_ms is not None:
                newest_mtime_ms = mtime_ms if newest_mtime_ms is None else max(newest_mtime_ms, mtime_ms)
            file_rows[name] = {
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": _safe_size(path),
                "modified_ms": mtime_ms,
            }
        cursor = _read_json(self.cursor_path)
        age_ms = timestamp_ms - newest_mtime_ms if newest_mtime_ms is not None else None
        stale = age_ms is None or age_ms > self.max_stale_sec * 1000
        required_present = self.markets_path.exists() and self.orders_path.exists() and self.trades_path.exists()
        if not required_present:
            status = "waiting_for_sidecar_files"
        elif stale:
            status = "stale"
        else:
            status = "ready"
        return {
            "enabled": self.enabled,
            "status": status,
            "ready": status == "ready",
            "repository": POLY_DATA_REPOSITORY,
            "integration_mode": "separate_sidecar_local_files_read_only",
            "license_boundary": "GPL-3.0 upstream runs separately; this adapter reads generated data only.",
            "max_stale_sec": self.max_stale_sec,
            "latest_file_age_ms": age_ms,
            "cursor": cursor,
            "schemas": {
                "markets": _csv_header(self.markets_path),
                "orders": _csv_header(self.orders_path),
                "trades": _csv_header(self.trades_path),
            },
            "files": file_rows,
            "safety": {
                "read_only": True,
                "mutates_upstream_files": False,
                "places_orders": False,
                "wallet_signing": False,
            },
        }

    def snapshot(self, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        timestamp = int(timestamp_ms if timestamp_ms is not None else _now_ms())
        if not self.enabled:
            return {
                "enabled": False,
                "status": "disabled",
                "ready": False,
                "repository": POLY_DATA_REPOSITORY,
                "integration_mode": "disabled",
                "license_boundary": "GPL-3.0 upstream is not imported into this project.",
                "safety": {
                    "read_only": True,
                    "mutates_upstream_files": False,
                    "places_orders": False,
                    "wallet_signing": False,
                },
            }
        if self.manifest_url:
            return self._remote_snapshot(timestamp_ms=timestamp)
        return self._local_snapshot(timestamp_ms=timestamp)


class PolyMakerShadowAdapter:
    """Compute maker diagnostics from the existing books without placing orders."""

    def __init__(self) -> None:
        self.enabled = _env_bool("POLY_MAKER_SHADOW_ENABLED", True)
        self.max_book_age_ms = max(100, int(os.getenv("POLY_MAKER_SHADOW_MAX_BOOK_AGE_MS", "5000")))
        self.min_spread = max(0.0, float(os.getenv("POLY_MAKER_SHADOW_MIN_SPREAD", "0.01")))
        self.min_depth_usd = max(0.0, float(os.getenv("POLY_MAKER_SHADOW_MIN_DEPTH_USD", "20")))
        self.quote_tick = max(0.0001, float(os.getenv("POLY_MAKER_SHADOW_QUOTE_TICK", "0.01")))
        self.merge_min_shares = max(0.0, float(os.getenv("POLY_MAKER_SHADOW_MERGE_MIN_SHARES", "5")))

    @staticmethod
    def _paper_inventory(snapshot: dict[str, Any]) -> dict[str, float]:
        paper = dict(snapshot.get("paper_portfolio") or {})
        positions = list(paper.get("open_positions") or [])
        yes_shares = 0.0
        no_shares = 0.0
        for position in positions:
            for order in list((position or {}).get("orders") or []):
                shares = float((order or {}).get("shares") or 0.0)
                if (order or {}).get("direction") == "YES":
                    yes_shares += shares
                elif (order or {}).get("direction") == "NO":
                    no_shares += shares
        return {
            "yes_shares": round(yes_shares, 8),
            "no_shares": round(no_shares, 8),
            "net_yes_shares": round(yes_shares - no_shares, 8),
            "mergeable_shares": round(min(yes_shares, no_shares), 8),
        }

    def _token_quote(self, book: dict[str, Any], *, timestamp_ms: int) -> dict[str, Any]:
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")
        book_timestamp = int(book.get("timestamp_ms") or 0)
        book_age_ms = timestamp_ms - book_timestamp if book_timestamp > 0 else None
        bid_depth = _book_depth_usd(book.get("bid_levels"))
        ask_depth = _book_depth_usd(book.get("ask_levels"))
        spread = float(best_ask) - float(best_bid) if best_bid is not None and best_ask is not None else None
        mid = (float(best_bid) + float(best_ask)) / 2 if spread is not None else None
        quote_bid = min(float(best_ask) - self.quote_tick, float(best_bid) + self.quote_tick) if spread is not None else None
        quote_ask = max(float(best_bid) + self.quote_tick, float(best_ask) - self.quote_tick) if spread is not None else None
        stale = book_age_ms is None or book_age_ms < 0 or book_age_ms > self.max_book_age_ms
        depth_ready = bid_depth >= self.min_depth_usd and ask_depth >= self.min_depth_usd
        spread_ready = spread is not None and spread >= self.min_spread
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round(mid, 8) if mid is not None else None,
            "spread": round(spread, 8) if spread is not None else None,
            "book_age_ms": book_age_ms,
            "bid_depth_usd": bid_depth,
            "ask_depth_usd": ask_depth,
            "book_imbalance": _imbalance(bid_depth, ask_depth),
            "shadow_quote_bid": round(quote_bid, 8) if quote_bid is not None else None,
            "shadow_quote_ask": round(quote_ask, 8) if quote_ask is not None else None,
            "ready": not stale and depth_ready and spread_ready,
            "reasons": [
                reason
                for reason, failed in (
                    ("book_stale", stale),
                    ("depth_insufficient", not depth_ready),
                    ("spread_too_narrow", not spread_ready),
                )
                if failed
            ],
        }

    def snapshot(self, status_payload: dict[str, Any], raw_snapshot: dict[str, Any], *, timestamp_ms: int | None = None) -> dict[str, Any]:
        timestamp = int(timestamp_ms if timestamp_ms is not None else _now_ms())
        market = dict(raw_snapshot.get("current_market") or {})
        books = dict(raw_snapshot.get("books") or {})
        yes_token = str(market.get("yes_token_id") or "")
        no_token = str(market.get("no_token_id") or "")
        yes_quote = self._token_quote(dict(books.get(yes_token) or {}), timestamp_ms=timestamp)
        no_quote = self._token_quote(dict(books.get(no_token) or {}), timestamp_ms=timestamp)
        inventory = self._paper_inventory(raw_snapshot)
        merge_candidate = inventory["mergeable_shares"] >= self.merge_min_shares
        market_ready = status_payload.get("market", {}).get("discovery_status") == "ready"
        ready = self.enabled and market_ready and yes_quote["ready"] and no_quote["ready"]
        return {
            "enabled": self.enabled,
            "status": "ready" if ready else "shadow_wait",
            "ready": ready,
            "repository": POLY_MAKER_REPOSITORY,
            "integration_mode": "paper_only_shadow_diagnostics",
            "yes": yes_quote,
            "no": no_quote,
            "paper_inventory": inventory,
            "merge_candidate": {
                "eligible": merge_candidate,
                "shares": inventory["mergeable_shares"],
                "minimum_shares": self.merge_min_shares,
                "action": "observe_only",
            },
            "safety": {
                "paper_only": True,
                "shadow_only": True,
                "places_orders": False,
                "cancels_orders": False,
                "wallet_signing": False,
                "position_merge_execution": False,
                "google_sheets_credentials_required": False,
            },
        }


_poly_data = PolyDataSidecarAdapter()
_poly_maker = PolyMakerShadowAdapter()
_latest: dict[str, Any] = {
    "updated_at_ms": 0,
    "poly_data": _poly_data.snapshot(),
    "poly_maker_shadow": {
        "enabled": _poly_maker.enabled,
        "status": "waiting_for_first_status",
        "ready": False,
        "repository": POLY_MAKER_REPOSITORY,
        "integration_mode": "paper_only_shadow_diagnostics",
        "safety": {
            "paper_only": True,
            "shadow_only": True,
            "places_orders": False,
            "wallet_signing": False,
        },
    },
}


def update_poly_integrations(status_payload: dict[str, Any], raw_snapshot: dict[str, Any]) -> dict[str, Any]:
    global _latest
    timestamp = _now_ms()
    _latest = {
        "updated_at_ms": timestamp,
        "poly_data": _poly_data.snapshot(timestamp_ms=timestamp),
        "poly_maker_shadow": _poly_maker.snapshot(status_payload, raw_snapshot, timestamp_ms=timestamp),
        "safety": {
            "live_execution_imported": False,
            "private_key_required": False,
            "wallet_signing_enabled": False,
            "paper_only": True,
        },
    }
    return dict(_latest)


def get_poly_integrations() -> dict[str, Any]:
    return dict(_latest)


def register_poly_integrations(app: FastAPI) -> None:
    @app.get("/integrations")
    async def integrations() -> JSONResponse:
        return JSONResponse(get_poly_integrations(), headers=NO_STORE_HEADERS)

    @app.get("/integrations/poly-data")
    async def poly_data() -> JSONResponse:
        return JSONResponse(get_poly_integrations().get("poly_data", {}), headers=NO_STORE_HEADERS)

    @app.get("/integrations/poly-maker-shadow")
    async def poly_maker_shadow() -> JSONResponse:
        return JSONResponse(get_poly_integrations().get("poly_maker_shadow", {}), headers=NO_STORE_HEADERS)
