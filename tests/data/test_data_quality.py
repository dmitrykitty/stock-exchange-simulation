"""Data quality and cross-source checks for Phase F1 (owners: D1, D2).

Verifies:
- Yahoo futures (GC=F, PL=F, HG=F) against official World Bank monthly prices;
- FRED WTI against Yahoo CL=F daily returns;
- EDGAR downloaded submissions match SIC and CIK in data/mapping/companies.csv;
- BEA 2022 and 2023 matrices stability and properties;
- outliers_report.csv and manifest.json completeness.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from marketsim import paths
from marketsim.data import api


def _skip_if_missing(fn, *args):
    try:
        return fn(*args)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def test_yahoo_futures_match_worldbank_monthly():
    daily = _skip_if_missing(api.load_commodities_daily)
    monthly = _skip_if_missing(api.load_commodities_monthly)

    daily_m = daily.resample("MS").mean()
    joined = pd.concat([daily_m, monthly], axis=1).dropna()
    assert len(joined) == 60

    gold_corr = joined["gold"].corr(joined["wb_gold"])
    plat_corr = joined["platinum"].corr(joined["wb_platinum"])
    cop_corr = joined["copper_usd_per_lb"].corr(joined["wb_copper_usd_per_mt"])

    assert gold_corr > 0.999
    assert plat_corr > 0.995
    assert cop_corr > 0.90


def test_fred_copper_matches_worldbank_copper():
    monthly = _skip_if_missing(api.load_commodities_monthly)
    assert len(monthly) == 60
    corr = monthly["fred_copper_usd_per_mt"].corr(monthly["wb_copper_usd_per_mt"])
    assert corr > 0.999


def test_wti_fred_vs_yahoo_cl_daily_returns():
    daily = _skip_if_missing(api.load_commodities_daily)
    yahoo_close = paths.raw_dir() / "yahoo" / "close_adj.csv"
    if not yahoo_close.exists():
        pytest.skip("yahoo/close_adj.csv not found")

    yf_df = pd.read_csv(yahoo_close, index_col=0)
    yf_df.index = pd.to_datetime(yf_df.index).tz_localize(None)
    cl = pd.to_numeric(yf_df["CL=F"], errors="coerce").reindex(daily.index).ffill(limit=3)

    ret_fred = np.log(daily["wti"]).diff()
    ret_cl = np.log(cl).diff()
    # Compare on days where neither day t nor day t-1 was forward-filled in FRED
    valid = ~daily["wti_filled"] & ~daily["wti_filled"].shift(1, fill_value=False)
    both = pd.concat([ret_fred[valid], ret_cl[valid]], axis=1).dropna()
    assert both.corr().iloc[0, 1] > 0.98
    assert daily["wti"].corr(cl) > 0.995


def test_edgar_submissions_reproduce_companies_csv():
    comp = api.load_companies()
    sub_dir = paths.raw_dir() / "edgar" / "submissions"
    if not sub_dir.exists():
        pytest.skip("data/raw/edgar/submissions not found")

    for row in comp.itertuples(index=False):
        cik_file = sub_dir / f"CIK{int(row.cik):010d}.json"
        assert cik_file.exists(), f"Missing EDGAR submission for {row.ticker}: {cik_file}"
        data = json.loads(cik_file.read_text(encoding="utf-8"))
        assert str(data["sic"]) == str(row.sic), f"{row.ticker}: EDGAR SIC {data['sic']} != {row.sic}"


def test_bea_2022_and_2023_consistency():
    a22 = _skip_if_missing(api.load_io_matrix, 2022)
    a23 = _skip_if_missing(api.load_io_matrix, 2023)
    assert list(a22.index) == list(a23.index)
    assert a22.shape == (71, 71)
    assert a22.sum().max() < 1.0
    assert np.max(np.abs(np.linalg.eigvals(a22.to_numpy()))) < 1.0


def test_outliers_report_and_manifest_exist():
    outliers_path = paths.processed_dir() / "outliers_report.csv"
    manifest_path = paths.raw_dir() / "manifest.json"
    if not outliers_path.exists() or not manifest_path.exists():
        pytest.skip("outliers_report.csv or manifest.json not generated yet")

    outliers = pd.read_csv(outliers_path)
    assert {"date", "series", "log_return", "explanation"} <= set(outliers.columns)
    assert len(outliers) > 0
    assert (outliers["explanation"].astype(str).str.len() > 0).all()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) >= 10
