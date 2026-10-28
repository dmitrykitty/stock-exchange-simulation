"""Check every data file against its contract: ``python -m marketsim.data.check``.

Prints "file -> OK / MISSING / INVALID" plus cross-file checks.
With ``--strict`` the exit code is non-zero when any file is missing (use it for the Friday check).
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from marketsim.data import api
from marketsim.data import schemas as S

IO_YEARS = (2022, 2023)


def _describe(df: pd.DataFrame) -> str:
    rng = ""
    if isinstance(df.index, pd.DatetimeIndex) and len(df):
        rng = f", {df.index.min().date()} -> {df.index.max().date()}"
    return f"{len(df)} rows x {df.shape[1]} columns{rng}"


def cross_checks(loaded: dict[str, pd.DataFrame]) -> list[str]:
    """Consistency between files: same calendar, same tickers, BEA codes exist."""
    problems = []
    cal = loaded.get("calendar")
    comp = loaded.get("companies")
    if comp is not None:
        tick = set(comp["ticker"])
        for name in ("stocks_close_adj", "stocks_close_raw", "stocks_volume"):
            if name in loaded and set(loaded[name].columns) != tick:
                problems.append(f"{name}: columns differ from the tickers in companies.csv")
    if cal is not None:
        for name in ("commodities_daily", "market_daily", "stocks_close_adj", "stocks_close_raw",
                     "stocks_volume", "returns_daily", "ff49_vw_daily"):
            if name in loaded and not loaded[name].index.equals(cal.index):
                problems.append(f"{name}: dates differ from the master calendar")
    if "ff49_vw_daily" in loaded and loaded["ff49_vw_daily"].shape[1] != 49:
        problems.append("ff49_vw_daily: expected 49 columns")
    for year in IO_YEARS:
        a = loaded.get(f"io_matrix_{year}")
        if a is None:
            continue
        if a.shape != (71, 71):
            problems.append(f"io_{year}_A: shape {a.shape}, expected (71, 71)")
        elif not a.index.equals(a.columns):
            problems.append(f"io_{year}_A: rows and columns must be the same industry codes in the same order")
        if comp is not None:
            missing = set(comp["bea_code"]) - set(a.index)
            if missing:
                problems.append(f"companies.csv: BEA codes not found in the {year} table: {sorted(missing)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="treat a missing file as an error")
    args = parser.parse_args(argv)

    jobs = [(spec.name, spec, {}) for spec in S.ALL_DATA_TABLES if spec not in (S.IO_MATRIX, S.IO_INDUSTRIES)]
    for year in IO_YEARS:
        jobs += [(f"io_matrix_{year}", S.IO_MATRIX, {"year": year}),
                 (f"io_industries_{year}", S.IO_INDUSTRIES, {"year": year})]

    loaded: dict[str, pd.DataFrame] = {}
    missing = invalid = 0
    for name, spec, fmt in jobs:
        try:
            df = api.read_table(spec, **fmt)
        except FileNotFoundError:
            missing += 1
            print(f"  MISSING  {name:22s} {api.table_path(spec, **fmt)}  (owner {spec.owner})")
        except S.ContractError as exc:
            invalid += 1
            print(f"  INVALID  {name:22s} {exc}")
        else:
            loaded[name] = df
            print(f"  OK       {name:22s} {_describe(df)}")

    cross = cross_checks(loaded)
    for p in cross:
        print(f"  LINK     {p}")
    print(f"\nTotal: OK {len(loaded)}, missing {missing}, invalid {invalid}, cross-file problems {len(cross)}")
    failed = invalid > 0 or len(cross) > 0 or (args.strict and missing > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
