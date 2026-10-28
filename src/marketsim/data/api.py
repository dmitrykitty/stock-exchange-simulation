"""Reading and writing data under contract. The rest of the project reads data **only** through this module.

Every loader validates its table against marketsim.data.schemas and raises ContractError when a file does
not follow the contract. A data problem then shows up immediately instead of as a strange simulation result
a week later.

Stability rule: the names, parameters and return formats of the public functions below are frozen
(contract). Implementation details may change freely; new helpers may be added (prefer ``_private`` names).

Example::

    from marketsim.data import api
    prices = api.load_stock_prices()          # DataFrame: date x tickers
    A = api.load_io_matrix(2023)              # DataFrame: industry code x industry code
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from marketsim import paths
from marketsim.data import schemas as S
from marketsim.data.schemas import ContractError, TableSpec, validate

__all__ = [
    "ContractError",
    "load_calendar",
    "load_commodities_daily",
    "load_commodities_monthly",
    "load_companies",
    "load_external",
    "load_ff49",
    "load_io_industries",
    "load_io_matrix",
    "load_market_daily",
    "load_returns_daily",
    "load_stock_prices",
    "load_stock_volume",
    "read_table",
    "table_path",
    "tickers",
    "write_table",
]


def table_path(spec: TableSpec, **fmt) -> Path:
    base = {
        "processed": paths.processed_dir(),
        "mapping": paths.mapping_dir(),
        "graph": paths.graph_dir(),
        "fixtures": paths.fixtures_dir(),
    }[spec.location]
    return base / spec.file.format(**fmt)


def _read_csv(path: Path, spec: TableSpec) -> pd.DataFrame:
    dtype = {c.name: "string" for c in spec.columns if c.kind == "s"}
    if spec.index is not None and spec.index.kind == "s":
        dtype[spec.index.name] = "string"
    df = pd.read_csv(path, dtype=dtype or None)
    for c in spec.columns:
        if c.kind == "M" and c.name in df.columns:
            df[c.name] = pd.to_datetime(df[c.name])
    if spec.index is not None:
        if spec.index.kind == "M":
            df[spec.index.name] = pd.to_datetime(df[spec.index.name])
        df = df.set_index(spec.index.name)
    return df


def read_table(spec: TableSpec, path: Path | None = None, **fmt) -> pd.DataFrame:
    """Read a parquet or csv file and validate it against its schema."""
    path = Path(path) if path is not None else table_path(spec, **fmt)
    if not path.exists():
        raise FileNotFoundError(
            f"{spec.name}: file {path} not found. How to produce it: docs/plans/phases/F1_data_collection.md "
            f"(owner: {spec.owner or 'see schema'})."
        )
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".csv":
        df = _read_csv(path, spec)
    else:
        raise ValueError(f"unsupported file type: {path}")
    return validate(df, spec)


def write_table(df: pd.DataFrame, spec: TableSpec, path: Path | None = None, **fmt) -> Path:
    """Validate a table and write it. Use it in clean.py so that every output follows the contract."""
    validate(df, spec)
    path = Path(path) if path is not None else table_path(spec, **fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path)
    elif path.suffix == ".csv":
        df.to_csv(path, index=spec.index is not None)
    else:
        raise ValueError(f"unsupported file type: {path}")
    return path


def load_calendar() -> pd.DataFrame:
    """Master calendar of NYSE trading days (index ``date``, column ``session``).

    Why: every daily series is aligned to it; one session of the simulation = one row.
    Used by: clean.py (alignment), graph weights (regressions), simulation (session -> date), validation.
    """
    return read_table(S.CALENDAR)


def load_commodities_daily() -> pd.DataFrame:
    """Daily commodity prices: wti, brent, henry_hub, gold, platinum, copper_usd_per_lb.

    Why: commodities are the external signals agents react to, and parents in the graph regressions.
    Used by: graph weights (D2), external-signal scenarios for the simulation (D2), validation (D1).
    """
    return read_table(S.COMMODITIES_DAILY)


def load_commodities_monthly() -> pd.DataFrame:
    """Official monthly commodity prices (World Bank, FRED).

    Why: cross-check of the daily futures from Yahoo (contract rolls, COMEX vs world prices).
    Used by: data tests and the data overview notebook (D1).
    """
    return read_table(S.COMMODITIES_MONTHLY)


def load_market_daily() -> pd.DataFrame:
    """Market index: sp500 level and the Fama-French market return.

    Why: the S&P 500 is an external signal in the simulation and a mandatory control in every weight
    regression (without it all company-to-company weights are inflated by common market moves).
    Used by: graph weights (D2), external-signal scenarios (D2), validation (D1).
    """
    return read_table(S.MARKET_DAILY)


def load_stock_prices(adjusted: bool = True) -> pd.DataFrame:
    """Daily close prices of the traded companies (columns = tickers).

    adjusted=True: splits + dividends, use for returns. adjusted=False: splits only, use for the price level
    on a date.
    Why: real stock prices teach the agents (graph weights), give start prices, and are the benchmark for
    validation. They are never fed into the running simulation.
    Used by: graph weights and start prices (D2), validation (D1).
    """
    return read_table(S.STOCKS_CLOSE_ADJ if adjusted else S.STOCKS_CLOSE_RAW)


def load_stock_volume() -> pd.DataFrame:
    """Daily traded volume in shares (columns = tickers).

    Why: the volume-volatility stylized fact and realistic order-size ranges.
    Used by: validation (D1), agent calibration ranges (S).
    """
    return read_table(S.STOCKS_VOLUME)


def load_returns_daily() -> pd.DataFrame:
    """Daily log returns of tickers, commodities and sp500 on the master calendar.

    Why: one table with everything the weight regressions need; computed once, consistently.
    Used by: graph weights (D2), validation and stylized facts (D1).
    """
    return read_table(S.RETURNS_DAILY)


def load_ff49() -> pd.DataFrame:
    """Daily returns of the 49 Fama-French industry portfolios.

    Why: industry-level validation; correlations of industry portfolios are cleaner than of single stocks.
    Used by: validation (D1).
    """
    return read_table(S.FF49_DAILY)


def load_companies() -> pd.DataFrame:
    """Traded companies and their BEA industry (data/mapping/companies.csv).

    Why: defines which assets exist in the simulation and links every company to an industry, which is how
    BEA input-output tables decide which company -> company edges make economic sense.
    Used by: everything (asset list), graph structure (D2), checks.
    """
    return read_table(S.COMPANIES)


def tickers() -> list[str]:
    """Tickers of the traded companies; their order is the asset order on the exchange."""
    return load_companies()["ticker"].tolist()


def load_io_matrix(year: int = 2023) -> pd.DataFrame:
    """BEA cost-share matrix (71 x 71): A.loc[j, i] = share of industry i's output spent on industry j.

    Why: the economic structure of the graph (which supplier -> customer edges exist). Not the weights.
    Used by: graph structure (D2).
    """
    return read_table(S.IO_MATRIX, year=year)


def load_io_industries(year: int = 2023) -> pd.DataFrame:
    """BEA industry names and output (margin is informational only).

    Why: readable industry names in the graph, reports and charts.
    Used by: graph structure and documentation (D2), visualization.
    """
    return read_table(S.IO_INDUSTRIES, year=year)


def load_external(scenario: str) -> pd.DataFrame:
    """External signals (commodities + index) per session for one scenario (contract K2).

    Looks in processed/ first, then fixtures/ (csv). Scenarios: real periods (e.g. "real_2024_2025") and
    synthetic shocks (e.g. "oil_shock").
    Why: this is the only real-world input of a running simulation.
    Used by: simulation (S), reference path (D2), metrics (D1).
    """
    processed = table_path(S.EXTERNAL, scenario=scenario)
    if processed.exists():
        return read_table(S.EXTERNAL, processed)
    fixture = paths.fixtures_dir() / f"external_{scenario}.csv"
    return read_table(S.EXTERNAL, fixture)
