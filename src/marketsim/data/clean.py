"""Cleaning: data/raw/ -> data/processed/ following marketsim.data.schemas (owners: D1, D2).

Skeleton. Each function builds one table and writes it with ``api.write_table``, which validates the schema.
Cleaning rules: docs/plans/phases/F1_data_collection.md.

Stability rule: the output tables are the contract (names, columns, types in schemas.py). How you build them
is up to you; split functions and add helpers freely.

Run: ``python -m marketsim.data.clean``, then ``python -m marketsim.data.check``.
"""

from __future__ import annotations

import sys


def build_calendar():
    """D1. NYSE trading days = days with stock prices (1255 days in 2021-2025) -> calendar.parquet.

    Why: one shared time axis; without it series silently misalign by holidays.
    """
    raise NotImplementedError


def build_commodities():
    """D1. commodities_daily (on the calendar, forward-fill at most 3 days, with flags) and commodities_monthly.

    Why: external signals and regression inputs must have a value on every trading day.
    """
    raise NotImplementedError


def build_market():
    """D1. market_daily: sp500 (FRED) and ff_mkt_ret (Fama-French Mkt-RF + RF, as a fraction).

    Why: market index for the simulation and for the regressions.
    """
    raise NotImplementedError


def build_stocks():
    """D1. stocks_close_adj, stocks_close_raw, stocks_volume, returns_daily.

    Why: inputs of the graph weights (D2), start prices, and validation benchmarks (D1).
    """
    raise NotImplementedError


def build_ff49():
    """D1. ff49_vw_daily: value-weighted section, percent -> fraction, -99.99 / -999 -> NaN.

    Why: industry-level validation.
    """
    raise NotImplementedError


def build_io(year: int):
    """D2. io_{year}_A and io_{year}_industries from the BEA Summary Use table.

    Why: the cost-share matrix decides which supplier -> customer edges exist in the graph.
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("clean.main is implemented by D1 and D2")


if __name__ == "__main__":
    sys.exit(main())
