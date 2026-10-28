"""Data contract: schema validation logic, the committed company mapping, and (when present) real data files.

Tests for real files are skipped until D1/D2 produce them; after that they pin the expected numbers from
docs/plans/phases/F1_data_collection.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketsim.data import api
from marketsim.data import schemas as S
from marketsim.data.schemas import ContractError, validate


def _dates(n):
    return pd.DatetimeIndex(pd.bdate_range("2024-01-02", periods=n), name="date")


def test_validate_accepts_a_correct_table():
    df = pd.DataFrame({"sp500": [1.0, 2.0], "ff_mkt_ret": [0.01, np.nan]}, index=_dates(2))
    validate(df, S.MARKET_DAILY)


@pytest.mark.parametrize(
    "df",
    [
        pd.DataFrame({"sp500": [1.0, np.nan], "ff_mkt_ret": [0.0, 0.0]}, index=_dates(2)),       # missing value
        pd.DataFrame({"ff_mkt_ret": [0.0, 0.0]}, index=_dates(2)),                               # missing column
        pd.DataFrame({"sp500": [1.0, 2.0], "ff_mkt_ret": [0.0, 0.0], "x": [1, 2]}, index=_dates(2)),  # extra
        pd.DataFrame({"sp500": [1.0, 2.0], "ff_mkt_ret": [0.0, 0.0]}, index=_dates(2).rename("day")),  # index name
        pd.DataFrame({"sp500": [1.0, 2.0], "ff_mkt_ret": [0.0, 0.0]}, index=_dates(2)[::-1]),    # unsorted
    ],
)
def test_validate_rejects_broken_tables(df):
    with pytest.raises(ContractError):
        validate(df, S.MARKET_DAILY)


def test_dynamic_columns_must_have_the_right_kind():
    good = pd.DataFrame({"AAA": [1.0, 2.0], "BBB": [3.0, 4.0]}, index=_dates(2))
    validate(good, S.STOCKS_CLOSE_ADJ)
    bad = good.assign(BBB=["x", "y"])
    with pytest.raises(ContractError):
        validate(bad, S.STOCKS_CLOSE_ADJ)


def test_write_and_read_roundtrip(tmp_path):
    df = pd.DataFrame({"session": np.arange(3)}, index=_dates(3))
    path = api.write_table(df, S.CALENDAR, tmp_path / "calendar.parquet")
    back = api.read_table(S.CALENDAR, path)
    assert back.equals(df)


def test_committed_company_mapping_follows_the_contract():
    comp = api.load_companies()
    assert comp["ticker"].is_unique
    assert set(comp["status"]) <= {"draft", "confirmed"}
    assert (comp["justification"].str.len() > 0).all()


def test_external_fixture_follows_the_contract():
    ext = api.load_external("oil_shock")
    assert list(ext.columns) == ["date", "wti", "sp500"]
    assert (ext[["wti", "sp500"]] > 0).all().all()


def _load_or_skip(loader, *args):
    try:
        return loader(*args)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def test_real_calendar():
    cal = _load_or_skip(api.load_calendar)
    assert len(cal.loc["2021":"2025"]) == 1255
    assert (cal["session"].to_numpy() == np.arange(len(cal))).all()


def test_real_market_index():
    m = _load_or_skip(api.load_market_daily)
    assert m["sp500"].notna().all()
    both = pd.concat([np.log(m["sp500"]).diff(), m["ff_mkt_ret"]], axis=1).dropna()
    assert both.corr().iloc[0, 1] > 0.99


def test_real_commodities_are_positive():
    c = _load_or_skip(api.load_commodities_daily)
    assert (c[["wti", "brent", "henry_hub", "gold", "platinum", "copper_usd_per_lb"]] > 0).all().all()


def test_real_stocks_match_company_list():
    prices = _load_or_skip(api.load_stock_prices)
    assert set(prices.columns) == set(api.tickers())


def test_real_ff49_has_49_industries():
    assert _load_or_skip(api.load_ff49).shape[1] == 49


def test_real_bea_matrix_reference_values():
    a = _load_or_skip(api.load_io_matrix, 2023)
    ind = _load_or_skip(api.load_io_industries, 2023)
    assert a.shape == (71, 71)
    assert abs(a.loc["324", "481"] - 0.1292) < 1e-3   # petroleum products in air transportation costs
    assert abs(a.loc["211", "324"] - 0.5077) < 1e-3   # oil and gas in refining costs
    assert abs(ind.loc["481", "output"] - 311_313) < 1
    assert a.sum().max() < 1
    assert np.max(np.abs(np.linalg.eigvals(a.to_numpy()))) < 1
    assert set(api.load_companies()["bea_code"]) <= set(a.index)
