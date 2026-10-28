"""Table schemas: which files exist, which columns and types they have.

This is the contract between the data owners (D1, D2) and everybody else.

Column kinds:
    "f" float, "i" integer, "n" any number (not bool), "b" bool, "s" string, "M" datetime.

``dynamic`` describes columns whose names depend on the data (tickers, industries): the names are not
fixed, but all such columns must have the ``dynamic`` kind.

Change a schema only following docs/model/contracts.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from pandas.api import types as pdt


class ContractError(ValueError):
    """A table does not follow its contract."""


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    nullable: bool = True


@dataclass(frozen=True)
class TableSpec:
    name: str
    file: str                       # file name; may contain {year} / {scenario}
    location: str                   # "processed" | "mapping" | "graph" | "fixtures" | "run"
    description: str
    index: Column | None = None
    columns: tuple[Column, ...] = ()
    dynamic: str | None = None      # kind of columns that are not listed in `columns`
    dynamic_nullable: bool = True
    allow_extra: bool = False       # whether extra columns of any type are allowed
    min_rows: int = 1
    owner: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


def _kind_ok(series_or_index, kind: str) -> bool:
    dt = series_or_index.dtype
    if kind == "f":
        return pdt.is_float_dtype(dt)
    if kind == "i":
        return pdt.is_integer_dtype(dt) and not pdt.is_bool_dtype(dt)
    if kind == "n":
        return pdt.is_numeric_dtype(dt) and not pdt.is_bool_dtype(dt)
    if kind == "b":
        return pdt.is_bool_dtype(dt)
    if kind == "s":
        return pdt.is_string_dtype(dt) or pdt.is_object_dtype(dt)
    if kind == "M":
        return pdt.is_datetime64_any_dtype(dt)
    raise ValueError(f"unknown kind {kind!r}")


def validate(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    """Check a table against its schema. Returns the table or raises ContractError listing every problem."""
    problems: list[str] = []
    if spec.index is not None:
        if df.index.name != spec.index.name:
            problems.append(f"index must be named {spec.index.name!r}, got {df.index.name!r}")
        elif not _kind_ok(df.index, spec.index.kind):
            problems.append(f"index {spec.index.name!r}: dtype {df.index.dtype}, expected kind {spec.index.kind!r}")
        if not spec.index.nullable and df.index.isna().any():
            problems.append(f"index {spec.index.name!r} has missing values")
        if not df.index.is_unique:
            problems.append(f"index {spec.index.name!r} has duplicates")
        if spec.index.kind == "M" and not df.index.is_monotonic_increasing:
            problems.append("dates in the index must be increasing")
    known = {c.name for c in spec.columns}
    for col in spec.columns:
        if col.name not in df.columns:
            problems.append(f"missing column {col.name!r}")
            continue
        if not _kind_ok(df[col.name], col.kind):
            problems.append(f"column {col.name!r}: dtype {df[col.name].dtype}, expected kind {col.kind!r}")
        if not col.nullable and df[col.name].isna().any():
            problems.append(f"column {col.name!r} has {int(df[col.name].isna().sum())} missing values")
    extra = [c for c in df.columns if c not in known]
    if extra and spec.dynamic is not None:
        for c in extra:
            if not _kind_ok(df[c], spec.dynamic):
                problems.append(f"column {c!r}: dtype {df[c].dtype}, expected kind {spec.dynamic!r}")
            elif not spec.dynamic_nullable and df[c].isna().any():
                problems.append(f"column {c!r} has {int(df[c].isna().sum())} missing values")
    elif extra and not spec.allow_extra:
        problems.append(f"unexpected columns: {extra}")
    if len(df) < spec.min_rows:
        problems.append(f"{len(df)} rows, at least {spec.min_rows} required")
    if problems:
        raise ContractError(f"{spec.name}: " + "; ".join(problems))
    return df


_DATE = Column("date", "M", nullable=False)

CALENDAR = TableSpec(
    "calendar", "calendar.parquet", "processed",
    "NYSE trading days of the analysis period (2021-2025: 1255 days). Master calendar for all daily series.",
    index=_DATE, columns=(Column("session", "i", nullable=False),), owner="D1",
)

COMMODITIES_DAILY = TableSpec(
    "commodities_daily", "commodities_daily.parquet", "processed",
    "Daily commodity prices on the master calendar, all > 0. Optional bool *_filled flags mark filled gaps.",
    index=_DATE,
    columns=(
        Column("wti", "f", False), Column("brent", "f", False), Column("henry_hub", "f", False),
        Column("gold", "f", False), Column("platinum", "f", False), Column("copper_usd_per_lb", "f", False),
    ),
    allow_extra=True, owner="D1",
)

COMMODITIES_MONTHLY = TableSpec(
    "commodities_monthly", "commodities_monthly.parquet", "processed",
    "Monthly commodity prices (World Bank, FRED); date = first day of the month. Used to cross-check futures.",
    index=_DATE, dynamic="f", owner="D1",
)

MARKET_DAILY = TableSpec(
    "market_daily", "market_daily.parquet", "processed",
    "Market index: sp500 = S&P 500 level (FRED); ff_mkt_ret = Fama-French market return (Mkt-RF + RF, fraction).",
    index=_DATE,
    columns=(Column("sp500", "f", False), Column("ff_mkt_ret", "f", True)),
    owner="D1",
)

STOCKS_CLOSE_ADJ = TableSpec(
    "stocks_close_adj", "stocks_close_adj.parquet", "processed",
    "Close prices adjusted for splits and dividends. Columns = tickers from companies.csv. Use for returns.",
    index=_DATE, dynamic="f", dynamic_nullable=False, owner="D1",
)

STOCKS_CLOSE_RAW = TableSpec(
    "stocks_close_raw", "stocks_close_raw.parquet", "processed",
    "Close prices adjusted for splits only. Use for the price level on a date (start prices).",
    index=_DATE, dynamic="f", dynamic_nullable=False, owner="D1",
)

STOCKS_VOLUME = TableSpec(
    "stocks_volume", "stocks_volume.parquet", "processed",
    "Daily traded volume in shares. Columns = tickers.",
    index=_DATE, dynamic="n", dynamic_nullable=False, owner="D1",
)

RETURNS_DAILY = TableSpec(
    "returns_daily", "returns_daily.parquet", "processed",
    "Daily log returns: tickers + commodities + sp500. The first row is NaN.",
    index=_DATE, dynamic="f", owner="D1",
)

FF49_DAILY = TableSpec(
    "ff49_vw_daily", "ff49_vw_daily.parquet", "processed",
    "Daily returns of the 49 Fama-French industry portfolios (value-weighted), as fractions.",
    index=_DATE, dynamic="f", owner="D1",
)

COMPANIES = TableSpec(
    "companies", "companies.csv", "mapping",
    "Companies traded by the agents and their mapping to BEA industries.",
    columns=(
        Column("ticker", "s", False), Column("name", "s", False), Column("cik", "i", False),
        Column("sic", "s", False), Column("sic_description", "s"), Column("bea_code", "s", False),
        Column("bea_name", "s"), Column("role", "s", False), Column("justification", "s", False),
        Column("status", "s", False),
    ),
    owner="D2",
    notes=("status: draft | confirmed",),
)

IO_MATRIX = TableSpec(
    "io_matrix", "io_{year}_A.parquet", "processed",
    "BEA cost shares (Summary level, 71 industries): A.loc[j, i] = share of industry i's output spent on j.",
    index=Column("code", "s", False), dynamic="f", dynamic_nullable=False, owner="D2",
)

IO_INDUSTRIES = TableSpec(
    "io_industries", "io_{year}_industries.parquet", "processed",
    "BEA industries: name, output (USD millions), operating margin (informational only).",
    index=Column("code", "s", False),
    columns=(Column("name", "s", False), Column("output", "f", False), Column("margin", "f")),
    owner="D2",
)

EXTERNAL = TableSpec(
    "external", "external_{scenario}.parquet", "processed",
    "External signals for the simulation per session: commodities and the index (contract K2). Levels > 0.",
    index=Column("session", "i", False), columns=(Column("date", "M", True),), dynamic="f",
    dynamic_nullable=False, owner="D2",
)

ALL_DATA_TABLES = (
    CALENDAR, COMMODITIES_DAILY, COMMODITIES_MONTHLY, MARKET_DAILY, STOCKS_CLOSE_ADJ, STOCKS_CLOSE_RAW,
    STOCKS_VOLUME, RETURNS_DAILY, FF49_DAILY, COMPANIES, IO_MATRIX, IO_INDUSTRIES,
)
