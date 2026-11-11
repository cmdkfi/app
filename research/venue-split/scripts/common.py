"""Shared loading for the venue_split_gap_risk scripts.

Run with the Liquidation-dataset venv python, e.g.
  ".../Liquidation dataset/scripts/.venv/bin/python3" -I part_a_gaps.py
"""
import os
import pandas as pd

import os
# The minute file is a release asset: research/liquidation-dataset/DATA.md, `data/binance_1m_ext.parquet`
# (81.1 MB, SHA-256 3b411dbd…), downloaded to that path. Override with $CMDK_BINANCE_1M.
PARQUET = os.environ.get("CMDK_BINANCE_1M", os.path.join(os.path.dirname(__file__), "../../liquidation-dataset/data/binance_1m_ext.parquet"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

# Corridor (docs/build-scope.md defaults as given in the brief)
TARGET_LTV = 0.50
ACT_LTV = 0.70
LIQ_LTV = 0.86          # Morpho market LLTV
START_LTVS = [0.50, 0.60, 0.70]
LATENCIES_S = [30, 120, 300]


def load_minutes(symbol: str) -> pd.DataFrame:
    """Minute klines for one symbol, sorted, with a contiguous-segment id.

    The parquet was pulled in windows around liquidation events, so it has gaps.
    A gap of more than 60 s between consecutive open_time values starts a new
    segment; no computation below spans a segment boundary.
    """
    df = pd.read_parquet(PARQUET)
    df = df[df.symbol == symbol].sort_values("open_time").reset_index(drop=True)
    df["t"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    gap = df.t.diff().dt.total_seconds()
    df["seg"] = (gap > 60).cumsum()
    return df[["t", "open", "high", "low", "close", "seg"]]


def seg_slice(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Rows between start and end (inclusive) that lie in one contiguous segment."""
    s = pd.Timestamp(start); e = pd.Timestamp(end)
    s = s.tz_localize("UTC") if s.tzinfo is None else s
    e = e.tz_localize("UTC") if e.tzinfo is None else e
    m = df[(df.t >= s) & (df.t <= e)]
    if m.seg.nunique() != 1:
        raise ValueError(f"window {start}..{end} spans a data gap (segments {sorted(m.seg.unique())})")
    return m.reset_index(drop=True)
