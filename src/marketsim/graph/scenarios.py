"""Build reproducible external-signal scenarios from the validated daily data tables."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.data import api
from marketsim.data import schemas as S

_REAL_SIGNAL_SOURCES = {
    "wti": ("commodities", "wti"),
    "henry_hub": ("commodities", "henry_hub"),
    "copper": ("commodities", "copper_usd_per_lb"),
    "gold": ("commodities", "gold"),
    "sp500": ("market", "sp500"),
}


def build_real_2024_2025() -> Path:
    """Write the 2024-2025 EXTERNAL scenario from the cleaned commodity, market, and calendar tables."""
    calendar = api.load_calendar()
    dates = calendar.index[
        (calendar.index >= pd.Timestamp("2024-01-01"))
        & (calendar.index <= pd.Timestamp("2025-12-31"))
    ]
    if dates.empty:
        raise ValueError("the master calendar has no trading sessions in 2024-2025")

    commodities = api.load_commodities_daily()
    market = api.load_market_daily()
    signals = pd.DataFrame(index=dates)
    for node, (source, column) in _REAL_SIGNAL_SOURCES.items():
        table = commodities if source == "commodities" else market
        signals[node] = table.loc[dates, column].to_numpy()

    values = signals.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("real external signal levels for 2024-2025 must be finite and > 0")

    result = signals.reset_index(names="date")
    result.insert(0, "session", np.arange(len(result), dtype=np.int64))
    result = result.set_index("session")
    return api.write_table(result, S.EXTERNAL, scenario="real_2024_2025")
