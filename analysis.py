from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List
import numpy as np
import pandas as pd

ISSUE_COLUMNS = [
    "rug_or_near_total_loss",
    "late_entry",
    "weak_buy_pressure",
    "high_dev_share",
    "too_few_holders",
    "low_volume",
]

@dataclass
class Report:
    total_trades: int
    win_rate: float
    avg_roi: float
    median_roi: float
    total_roi: float
    max_drawdown: float
    issue_breakdown: Dict[str, int]
    smart_money: List[Dict[str, Any]]
    strategy_breakdown: List[Dict[str, Any]]
    recommended_config: Dict[str, Any]
    daily_roi: List[Dict[str, Any]]
    equity_curve: List[Dict[str, Any]]

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)


def _load_json(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return pd.DataFrame()
    try:
        if text.startswith("["):
            data = json.loads(text)
        else:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
    except Exception:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    return df


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    defaults = {
        "roi": 0.0,
        "wallet": "unknown",
        "mint": "unknown",
        "buy_ratio": np.nan,
        "dev_percent": np.nan,
        "holders": np.nan,
        "volume_sol": np.nan,
        "alpha_score": np.nan,
        "strategy": "unknown",
        "entry_delay_sec": np.nan,
        "timestamp": pd.Timestamp.utcnow().isoformat(),
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["timestamp"] = df["timestamp"].fillna(pd.Timestamp.utcnow())
    num_cols = ["roi","buy_ratio","dev_percent","holders","volume_sol","alpha_score","entry_delay_sec"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def classify_issues(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rug_or_near_total_loss"] = df["roi"] <= -0.8
    df["late_entry"] = df["entry_delay_sec"].fillna(0) > 30
    df["weak_buy_pressure"] = df["buy_ratio"].fillna(1) < 0.65
    df["high_dev_share"] = df["dev_percent"].fillna(0) > 0.12
    df["too_few_holders"] = df["holders"].fillna(999999) < 50
    df["low_volume"] = df["volume_sol"].fillna(999999) < 15
    return df


def recommend_config(df: pd.DataFrame) -> Dict[str, Any]:
    total = max(len(df), 1)
    rugs = int(df["rug_or_near_total_loss"].sum())
    late = int(df["late_entry"].sum())
    weak = int(df["weak_buy_pressure"].sum())
    high_dev = int(df["high_dev_share"].sum())
    few_holders = int(df["too_few_holders"].sum())
    low_volume = int(df["low_volume"].sum())
    win_rate = float((df["roi"] > 0).mean()) if len(df) else 0.0
    rec = {
        "MIN_BUY_RATIO": 0.72,
        "MAX_DEV_PERCENT": 0.10,
        "MIN_HOLDERS": 60,
        "MIN_VOLUME_SOL": 18,
        "MAX_ENTRY_DELAY_SEC": 25,
        "MIN_ALPHA_SCORE": 0.75,
        "TP_MULTIPLIER": 1.4,
        "SL_MULTIPLIER": 0.78,
    }
    if rugs / total > 0.25:
        rec["MAX_DEV_PERCENT"] = 0.07
        rec["MIN_HOLDERS"] = 80
    if late / total > 0.25:
        rec["MAX_ENTRY_DELAY_SEC"] = 18
    if weak / total > 0.20:
        rec["MIN_BUY_RATIO"] = 0.76
    if high_dev / total > 0.20:
        rec["MAX_DEV_PERCENT"] = min(rec["MAX_DEV_PERCENT"], 0.06)
    if few_holders / total > 0.20:
        rec["MIN_HOLDERS"] = max(rec["MIN_HOLDERS"], 90)
    if low_volume / total > 0.20:
        rec["MIN_VOLUME_SOL"] = 20
    if win_rate < 0.45:
        rec["MIN_ALPHA_SCORE"] = 0.80
        rec["TP_MULTIPLIER"] = 1.3
        rec["SL_MULTIPLIER"] = 0.82
    return rec


def smart_money_ranking(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    grp = df.groupby("wallet").agg(
        trades=("roi", "count"),
        win_rate=("roi", lambda s: float((s > 0).mean())),
        avg_roi=("roi", "mean"),
        median_roi=("roi", "median"),
        alpha_mean=("alpha_score", "mean"),
    ).reset_index()
    grp["score"] = (
        grp["win_rate"].fillna(0) * 0.45 +
        grp["avg_roi"].clip(-1, 3).fillna(0) * 0.30 +
        np.log1p(grp["trades"]).fillna(0) * 0.15 +
        grp["alpha_mean"].fillna(0) * 0.10
    )
    grp = grp.sort_values(["score", "trades"], ascending=[False, False]).head(20)
    return grp.to_dict(orient="records")


def strategy_breakdown(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    grp = df.groupby("strategy").agg(
        trades=("roi", "count"),
        win_rate=("roi", lambda s: float((s > 0).mean())),
        avg_roi=("roi", "mean"),
        median_roi=("roi", "median"),
    ).reset_index().sort_values("avg_roi", ascending=False)
    return grp.to_dict(orient="records")


def generate_report(path: str | Path = "data/trades.json") -> Report:
    df = _prepare(_load_json(path))
    if df.empty:
        return Report(0,0,0,0,0,0,{k:0 for k in ISSUE_COLUMNS},[],[],recommend_config(pd.DataFrame(columns=["roi"])),[],[])
    df = classify_issues(df)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["equity"] = (1 + df["roi"].fillna(0)).cumprod()
    peak = df["equity"].cummax()
    drawdown = (df["equity"] / peak) - 1
    daily = df.set_index("timestamp").resample("D")["roi"].sum().reset_index()
    issues = {k: int(df[k].sum()) for k in ISSUE_COLUMNS}
    equity_curve = df[["timestamp", "equity"]].assign(timestamp=lambda x: x["timestamp"].astype(str)).to_dict(orient="records")
    daily_roi = daily.assign(timestamp=lambda x: x["timestamp"].astype(str)).to_dict(orient="records")
    return Report(
        total_trades=int(len(df)),
        win_rate=float((df["roi"] > 0).mean()),
        avg_roi=float(df["roi"].mean()),
        median_roi=float(df["roi"].median()),
        total_roi=float(df["roi"].sum()),
        max_drawdown=float(drawdown.min()),
        issue_breakdown=issues,
        smart_money=smart_money_ranking(df),
        strategy_breakdown=strategy_breakdown(df),
        recommended_config=recommend_config(df),
        daily_roi=daily_roi,
        equity_curve=equity_curve,
    )
