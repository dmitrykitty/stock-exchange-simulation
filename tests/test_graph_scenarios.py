"""Tests for constructing the real external-signal table."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketsim.data import api
from marketsim.data.schemas import EXTERNAL, validate
from marketsim.graph import scenarios


def test_synthetic_external_scenarios_follow_contract():
    down = api.load_external("oil_shock_down")
    flat = api.load_external("flat")

    validate(down, EXTERNAL)
    validate(flat, EXTERNAL)
    graph_external_nodes = ["wti", "henry_hub", "copper", "gold", "sp500"]
    assert down[graph_external_nodes].notna().all().all()
    assert flat[graph_external_nodes].nunique().eq(1).all()
    assert down.loc[5:, "wti"].eq(80.0).all()


@pytest.mark.parametrize(("copper", "column", "scenario"), [
    ("lme", "copper_lme_usd_per_lb", "real_2024_2025"),
    ("comex", "copper_usd_per_lb", "real_2024_2025_comex"),
])
def test_build_real_external_uses_calendar_and_external_contract(monkeypatch, tmp_path, copper, column, scenario):
    dates = pd.date_range("2024-01-02", periods=3, freq="B", name="date")
    calendar = pd.DataFrame({"session": [250, 251, 252]}, index=dates)
    commodities = pd.DataFrame(
        {
            "wti": [70.0, 71.0, 72.0],
            "henry_hub": [2.0, 2.1, 2.2],
            "copper_usd_per_lb": [3.8, 3.9, 4.0],
            "copper_lme_usd_per_lb": [3.7, 3.75, 3.8],
            "gold": [2000.0, 2001.0, 2002.0],
        },
        index=dates,
    )
    market = pd.DataFrame({"sp500": [4700.0, 4710.0, 4720.0]}, index=dates)
    monkeypatch.setattr(scenarios.api, "load_calendar", lambda: calendar)
    monkeypatch.setattr(scenarios.api, "load_commodities_daily", lambda: commodities)
    monkeypatch.setattr(scenarios.api, "load_market_daily", lambda: market)
    written = {}

    def write_table(frame, spec, **kwargs):
        assert spec is EXTERNAL
        assert kwargs == {"scenario": scenario}
        written["frame"] = frame
        validate(frame, spec)
        return tmp_path / f"external_{scenario}.parquet"

    monkeypatch.setattr(scenarios.api, "write_table", write_table)
    path = scenarios.build_real_2024_2025(copper=copper)

    assert path.name == f"external_{scenario}.parquet"
    result = written["frame"]
    assert result.index.tolist() == [0, 1, 2]
    assert result.index.name == "session"
    assert result.columns.tolist() == ["date", "wti", "henry_hub", "copper", "gold", "sp500"]
    assert result["date"].tolist() == list(dates)
    assert np.array_equal(result["copper"].to_numpy(), commodities[column].to_numpy())


def test_build_real_external_rejects_unknown_copper_source():
    with pytest.raises(ValueError, match="copper"):
        scenarios.build_real_2024_2025(copper="shanghai")
