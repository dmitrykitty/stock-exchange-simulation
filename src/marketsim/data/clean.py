"""Cleaning: data/raw/ -> data/processed/ following marketsim.data.schemas (owners: D1, D2).

Skeleton. Each function builds one table and writes it with ``api.write_table``, which validates the schema.
Cleaning rules: docs/plans/phases/F1_data_collection.md.

Stability rule: the output tables are the contract (names, columns, types in schemas.py). How you build them
is up to you; split functions and add helpers freely.

Run: ``python -m marketsim.data.clean``, then ``python -m marketsim.data.check``.
"""

from __future__ import annotations

import argparse
import sys
import warnings
import zipfile
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim import paths
from marketsim.data import api
from marketsim.data import schemas as S

PERIOD_START = "2021-01-01"
PERIOD_END = "2025-12-31"
IO_YEARS = (2022, 2023)


def _read_yahoo_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "date"
    df = df.sort_index()
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.loc[PERIOD_START:PERIOD_END]


def _read_fred_csv(series_id: str) -> pd.Series:
    path = paths.raw_dir() / "fred" / f"{series_id}.csv"
    df = pd.read_csv(path)
    date_col = df.columns[0]
    val_col = df.columns[1]
    s = pd.Series(
        pd.to_numeric(df[val_col], errors="coerce").to_numpy(dtype=float),
        index=pd.DatetimeIndex(pd.to_datetime(df[date_col])).tz_localize(None),
        name=series_id,
    )
    s.index.name = "date"
    return s.sort_index()


def _ffill_max3(series: pd.Series, calendar_index: pd.DatetimeIndex, name: str) -> tuple[pd.Series, pd.Series]:
    """Align a series to the master calendar and forward-fill up to 3 consecutive trading days."""
    # Combine original dates and calendar dates, sort, forward-fill at most 3 steps on calendar
    aligned = series.reindex(calendar_index)
    missing_before = aligned.isna()
    filled = aligned.ffill(limit=3)
    # If a gap is at the very first calendar day (e.g. 2021-01-04), look back up to 5 calendar days in full series
    if filled.isna().any():
        full_union = series.reindex(series.index.union(calendar_index)).sort_index().ffill(limit=3)
        filled = filled.fillna(full_union.reindex(calendar_index))
    still_missing = filled.isna()
    if still_missing.any():
        dates_str = ", ".join(str(d.date()) for d in calendar_index[still_missing])
        warnings.warn(f"{name}: {int(still_missing.sum())} gaps longer than 3 days remain NaN ({dates_str})")
    flag = (missing_before & filled.notna()).astype(bool)
    return filled.astype(float), flag


def build_calendar() -> pd.DataFrame:
    """D1. NYSE trading days = days with stock prices (1255 days in 2021-2025) -> calendar.parquet.

    Why: one shared time axis; without it series silently misalign by holidays.
    """
    stock_tickers = api.tickers()
    adj = _read_yahoo_csv(paths.raw_dir() / "yahoo" / "close_adj.csv")
    present = [t for t in stock_tickers if t in adj.columns]
    stocks_only = adj[present].dropna(how="all")
    dates = pd.DatetimeIndex(stocks_only.index, name="date")
    cal = pd.DataFrame({"session": np.arange(len(dates), dtype=np.int64)}, index=dates)
    api.write_table(cal, S.CALENDAR)
    return cal


def build_commodities() -> tuple[pd.DataFrame, pd.DataFrame]:
    """D1. commodities_daily (on the calendar, forward-fill at most 3 days, with flags) and commodities_monthly.

    Why: external signals and regression inputs must have a value on every trading day.
    """
    cal = api.load_calendar()
    cal_idx = cal.index

    wti_s, wti_flag = _ffill_max3(_read_fred_csv("DCOILWTICO"), cal_idx, "wti")
    brent_s, brent_flag = _ffill_max3(_read_fred_csv("DCOILBRENTEU"), cal_idx, "brent")
    hh_s, hh_flag = _ffill_max3(_read_fred_csv("DHHNGSP"), cal_idx, "henry_hub")

    yahoo_adj = _read_yahoo_csv(paths.raw_dir() / "yahoo" / "close_adj.csv")
    gold_s, gold_flag = _ffill_max3(yahoo_adj["GC=F"], cal_idx, "gold")
    plat_s, plat_flag = _ffill_max3(yahoo_adj["PL=F"], cal_idx, "platinum")
    cop_s, cop_flag = _ffill_max3(yahoo_adj["HG=F"], cal_idx, "copper_usd_per_lb")

    daily = pd.DataFrame(
        {
            "wti": wti_s,
            "brent": brent_s,
            "henry_hub": hh_s,
            "gold": gold_s,
            "platinum": plat_s,
            "copper_usd_per_lb": cop_s,
            "wti_filled": wti_flag,
            "brent_filled": brent_flag,
            "henry_hub_filled": hh_flag,
            "gold_filled": gold_flag,
            "platinum_filled": plat_flag,
            "copper_usd_per_lb_filled": cop_flag,
        },
        index=cal_idx,
    )
    price_cols = ["wti", "brent", "henry_hub", "gold", "platinum", "copper_usd_per_lb"]
    if not (daily[price_cols] > 0).all().all():
        raise ValueError("All commodity daily prices in the analysis period must be > 0")
    api.write_table(daily, S.COMMODITIES_DAILY)

    # Monthly commodities: FRED PCOPPUSDM + World Bank Pink Sheet
    fred_copper_m = _read_fred_csv("PCOPPUSDM").loc[PERIOD_START:PERIOD_END]
    wb_path = paths.raw_dir() / "worldbank" / "CMO-Historical-Data-Monthly.xlsx"
    pk = pd.read_excel(wb_path, sheet_name="Monthly Prices", header=4).iloc[1:]
    raw_dates = pk.iloc[:, 0].astype(str).str.strip()
    valid_mask = raw_dates.str.match(r"^\d{4}M\d{2}$")
    pk = pk.loc[valid_mask].copy()
    pk.index = pd.to_datetime(raw_dates.loc[valid_mask].str.replace("M", "-"), format="%Y-%m")
    pk.index.name = "date"
    pk = pk.loc[PERIOD_START:PERIOD_END]

    def _find_col(prefix: str) -> pd.Series:
        matches = [c for c in pk.columns if str(c).strip().startswith(prefix)]
        if not matches:
            raise KeyError(f"Column starting with {prefix!r} not found in World Bank sheet: {list(pk.columns)}")
        return pd.to_numeric(pk[matches[0]], errors="coerce").astype(float)

    monthly_idx = fred_copper_m.index.union(pk.index).sort_values()
    monthly_idx.name = "date"
    monthly = pd.DataFrame(
        {
            "fred_copper_usd_per_mt": fred_copper_m.reindex(monthly_idx).astype(float),
            "wb_wti": _find_col("Crude oil, WTI").reindex(monthly_idx),
            "wb_brent": _find_col("Crude oil, Brent").reindex(monthly_idx),
            "wb_henry_hub": _find_col("Natural gas, US").reindex(monthly_idx),
            "wb_copper_usd_per_mt": _find_col("Copper").reindex(monthly_idx),
            "wb_gold": _find_col("Gold").reindex(monthly_idx),
            "wb_platinum": _find_col("Platinum").reindex(monthly_idx),
        },
        index=monthly_idx,
    )
    api.write_table(monthly, S.COMMODITIES_MONTHLY)
    return daily, monthly


def _read_french_factors() -> pd.DataFrame:
    zip_path = paths.raw_dir() / "french" / "F-F_Research_Data_Factors_daily_CSV.zip"
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        text = zf.read(csv_names[0]).decode("utf-8", errors="ignore")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if "Mkt-RF" in line and "RF" in line)
    data_lines = [lines[start_idx]]
    for line in lines[start_idx + 1 :]:
        s = line.strip()
        if not s:
            break
        first_tok = s.split(",")[0].strip()
        if not first_tok.isdigit() or len(first_tok) != 8:
            break
        data_lines.append(line)
    df = pd.read_csv(StringIO("\n".join(data_lines)), index_col=0)
    df.index = pd.to_datetime(df.index.astype(str).str.strip(), format="%Y%m%d")
    df.index.name = "date"
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.replace([-99.99, -999], np.nan) / 100.0
    return df.sort_index()


def build_market() -> pd.DataFrame:
    """D1. market_daily: sp500 (FRED) and ff_mkt_ret (Fama-French Mkt-RF + RF, as a fraction).

    Why: market index for the simulation and for the regressions.
    """
    cal = api.load_calendar()
    cal_idx = cal.index

    sp500_raw = _read_fred_csv("SP500")
    sp500_s, _ = _ffill_max3(sp500_raw, cal_idx, "sp500")

    ff = _read_french_factors()
    ff_mkt = (ff["Mkt-RF"] + ff["RF"]).reindex(cal_idx).astype(float)

    market = pd.DataFrame(
        {
            "sp500": sp500_s.astype(float),
            "ff_mkt_ret": ff_mkt,
        },
        index=cal_idx,
    )
    api.write_table(market, S.MARKET_DAILY)
    return market


def build_stocks() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """D1. stocks_close_adj, stocks_close_raw, stocks_volume, returns_daily.

    Why: inputs of the graph weights (D2), start prices, and validation benchmarks (D1).
    """
    cal = api.load_calendar()
    cal_idx = cal.index
    stock_tickers = api.tickers()

    adj_all = _read_yahoo_csv(paths.raw_dir() / "yahoo" / "close_adj.csv")
    raw_all = _read_yahoo_csv(paths.raw_dir() / "yahoo" / "close_raw.csv")
    vol_all = _read_yahoo_csv(paths.raw_dir() / "yahoo" / "volume.csv")

    close_adj = adj_all.reindex(index=cal_idx, columns=stock_tickers).astype(float)
    close_raw = raw_all.reindex(index=cal_idx, columns=stock_tickers).astype(float)
    volume = vol_all.reindex(index=cal_idx, columns=stock_tickers).astype(float)

    api.write_table(close_adj, S.STOCKS_CLOSE_ADJ)
    api.write_table(close_raw, S.STOCKS_CLOSE_RAW)
    api.write_table(volume, S.STOCKS_VOLUME)

    comm = api.load_commodities_daily()
    mkt = api.load_market_daily()

    comm_levels = pd.DataFrame(
        {
            "wti": comm["wti"],
            "brent": comm["brent"],
            "henry_hub": comm["henry_hub"],
            "gold": comm["gold"],
            "platinum": comm["platinum"],
            "copper_usd_per_lb": comm["copper_usd_per_lb"],
            # F2 names the graph node "copper"; keep this alias in the same USD/lb units.
            "copper": comm["copper_usd_per_lb"],
        },
        index=cal_idx,
    )
    levels = pd.concat([close_adj, comm_levels, mkt[["sp500"]]], axis=1)
    returns = np.log(levels / levels.shift(1)).astype(float)
    api.write_table(returns, S.RETURNS_DAILY)

    _build_outliers_report(returns)
    return close_adj, close_raw, volume, returns


def _build_outliers_report(returns: pd.DataFrame) -> Path:
    """Write data/processed/outliers_report.csv for all days with |daily log return| > 0.15."""
    rows = []
    # Avoid duplicate reporting of copper_usd_per_lb and copper alias
    cols = [c for c in returns.columns if c != "copper"]
    for col in cols:
        s = returns[col].dropna()
        extreme = s[s.abs() > 0.15]
        for dt, val in extreme.items():
            dt_str = str(dt.date())
            rows.append(
                {
                    "date": dt_str,
                    "series": col,
                    "log_return": round(float(val), 6),
                    "pct_change": round(float(np.exp(val) - 1.0) * 100.0, 2),
                }
            )
    out_df = pd.DataFrame(rows, columns=["date", "series", "log_return", "pct_change"])
    explanations = pd.read_csv(paths.mapping_dir() / "outlier_explanations.csv", dtype=str)
    out_df = out_df.merge(explanations, on=["date", "series"], how="left", validate="many_to_one")
    out_df["explanation"] = out_df["explanation"].fillna("unexplained")
    out_df["source_url"] = out_df["source_url"].fillna("")
    if not out_df.empty:
        out_df = out_df.sort_values(["date", "series"]).reset_index(drop=True)
    out_path = paths.processed_dir() / "outliers_report.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    explained = out_df["explanation"].ne("unexplained")
    print(f"[clean] outliers: {int(explained.sum())}/{len(out_df)} have sourced explanations")
    if not explained.all():
        counts = out_df.loc[~explained, "series"].value_counts().to_dict()
        print(f"[clean] unexplained outliers by series: {counts}")
    return out_path


def build_ff49() -> pd.DataFrame:
    """D1. ff49_vw_daily: value-weighted section, percent -> fraction, -99.99 / -999 -> NaN.

    Why: industry-level validation.
    """
    cal = api.load_calendar()
    cal_idx = cal.index

    zip_path = paths.raw_dir() / "french" / "49_Industry_Portfolios_daily_CSV.zip"
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        text = zf.read(csv_names[0]).decode("utf-8", errors="ignore")

    lines = text.splitlines()
    a = next(i for i, line in enumerate(lines) if "Value Weighted Returns -- Daily" in line)
    b = next(i for i, line in enumerate(lines) if "Equal Weighted Returns -- Daily" in line)

    section_lines = [lines[a + 1]]
    for line in lines[a + 2 : b]:
        s = line.strip()
        if not s:
            continue
        first_tok = s.split(",")[0].strip()
        if first_tok.isdigit() and len(first_tok) == 8:
            section_lines.append(line)

    vw = pd.read_csv(StringIO("\n".join(section_lines)), index_col=0)
    vw.index = pd.to_datetime(vw.index.astype(str).str.strip(), format="%Y%m%d")
    vw.index.name = "date"
    vw.columns = [c.strip() for c in vw.columns]
    for c in vw.columns:
        vw[c] = pd.to_numeric(vw[c], errors="coerce")
    vw = vw.replace([-99.99, -999], np.nan) / 100.0
    vw = vw.sort_index().reindex(cal_idx).astype(float)
    api.write_table(vw, S.FF49_DAILY)
    return vw


def build_io(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """D2. io_{year}_A and io_{year}_industries from the BEA Summary Use table.

    Why: the cost-share matrix decides which supplier -> customer edges exist in the graph.
    """
    from marketsim.data.fetch import BEA_XLSX_NAME

    bea_dir = paths.raw_dir() / "bea"
    xlsx_path = bea_dir / BEA_XLSX_NAME
    if not xlsx_path.exists():
        zip_path = bea_dir / "AllTablesIO.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [n for n in zf.namelist() if n.endswith(BEA_XLSX_NAME)]
            xlsx_path.write_bytes(zf.read(members[0]))

    x = pd.read_excel(xlsx_path, sheet_name=str(year), header=5)
    x = x.rename(columns={x.columns[0]: "code", x.columns[1]: "name"}).iloc[1:].copy()
    x["code"] = x["code"].astype(object).where(x["code"].notna(), None)
    x["code"] = x["code"].apply(lambda v: str(v).strip() if v is not None else None)
    x["name"] = x["name"].astype(str).str.strip()

    def _num(s: pd.Series) -> pd.Series:
        return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)

    col_strs = [str(c).strip() for c in x.columns]
    x.columns = col_strs
    valid_codes = set(x["code"].dropna())
    industries = [c for c in x.columns if c in valid_codes]

    output = _num(x.loc[x["name"] == "Total Industry Output", industries].iloc[0])
    use_rows = x[x["code"].isin(industries)].drop_duplicates(subset=["code"], keep="first")
    use = use_rows.set_index("code").reindex(industries)[industries].apply(_num)

    a_mat = (use / output).astype(float)
    a_mat.index = a_mat.index.astype(str)
    a_mat.index.name = "code"
    a_mat.columns = [str(c) for c in a_mat.columns]

    margin = (_num(x.loc[x["code"] == "V003", industries].iloc[0]) / output).astype(float)
    names_map = use_rows.set_index("code")["name"].reindex(industries).astype(str)

    ind_df = pd.DataFrame(
        {
            "name": names_map,
            "output": output.reindex(industries).astype(float),
            "margin": margin.reindex(industries).astype(float),
        },
        index=pd.Index(industries, dtype="string", name="code"),
    )

    api.write_table(a_mat, S.IO_MATRIX, year=year)
    api.write_table(ind_df, S.IO_INDUSTRIES, year=year)
    return a_mat, ind_df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="comma-separated subset: calendar,commodities,market,stocks,ff49,io",
    )
    args = parser.parse_args(argv)

    all_steps = ("calendar", "commodities", "market", "stocks", "ff49", "io")
    if args.only:
        selected = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in selected if s not in all_steps]
        if unknown:
            print(f"Unknown steps: {unknown}. Valid: {all_steps}", file=sys.stderr)
            return 2
    else:
        selected = list(all_steps)

    if "calendar" in selected:
        cal = build_calendar()
        print(f"[clean] calendar: {len(cal)} trading days ({cal.index.min().date()} .. {cal.index.max().date()})")

    if "commodities" in selected:
        daily, monthly = build_commodities()
        print(f"[clean] commodities_daily: {daily.shape}, commodities_monthly: {monthly.shape}")

    if "market" in selected:
        mkt = build_market()
        print(f"[clean] market_daily: {mkt.shape}")

    if "stocks" in selected:
        adj, _raw, _vol, ret = build_stocks()
        print(f"[clean] stocks: {adj.shape}, returns_daily: {ret.shape}")

    if "ff49" in selected:
        ff49 = build_ff49()
        print(f"[clean] ff49_vw_daily: {ff49.shape}")

    if "io" in selected:
        for y in IO_YEARS:
            a_mat, ind_df = build_io(y)
            print(f"[clean] io_{y}: A={a_mat.shape}, industries={ind_df.shape}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
