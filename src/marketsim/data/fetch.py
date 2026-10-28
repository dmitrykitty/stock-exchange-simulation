"""Download raw data into data/raw/ (owners: D1 for time series, D2 for BEA and EDGAR).

Skeleton: the functions are specified, the implementation follows docs/plans/phases/F1_data_collection.md
(working code snippets and expected numbers are there). Rules:
- a file lands in data/raw/ exactly as the server sent it and is never edited by hand;
- every downloaded file is recorded in data/raw/manifest.json: url, download date, size, sha256;
- a second run does not re-download existing files (``--force`` does).

Stability rule: keep the function names and parameters; add helpers and options freely.

Run: ``python -m marketsim.data.fetch [--force] [--only fred,yahoo,...]``
"""

from __future__ import annotations

import sys

START, END = "2021-01-01", "2026-01-01"  # END is exclusive in yfinance

FRED_DAILY = ("DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "SP500")
FRED_MONTHLY = ("PCOPPUSDM",)
YAHOO_FUTURES = ("CL=F", "NG=F", "HG=F", "GC=F", "PL=F")


def fetch_fred(series_id: str, force: bool = False):
    """D1. CSV without a key: https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID> -> data/raw/fred/<ID>.csv

    Why: oil, gas (external signals), S&P 500 (signal + regression control), monthly copper (cross-check).
    """
    raise NotImplementedError


def fetch_yahoo(tickers: list[str], adjusted: bool, force: bool = False):
    """D1. yfinance==1.7.0, START..END -> data/raw/yahoo/close_{adj|raw}.csv and volume.csv

    Why: stock prices (graph weights, start prices, validation) and metal futures (external signals).
    """
    raise NotImplementedError


def fetch_french(force: bool = False):
    """D1. 49_Industry_Portfolios_daily_CSV.zip and F-F_Research_Data_Factors_daily_CSV.zip -> data/raw/french/

    Why: industry-level validation and a backup market return.
    """
    raise NotImplementedError


def fetch_worldbank(force: bool = False):
    """D1. CMO-Historical-Data-Monthly.xlsx (take the link from the web page) -> data/raw/worldbank/

    Why: official monthly prices to cross-check Yahoo futures; backup source for gold and platinum.
    """
    raise NotImplementedError


def fetch_bea(force: bool = False):
    """D2. AllTablesIO.zip -> data/raw/bea/ (needs IOUse_Before_Redefinitions_PRO_1997-2023_Summary.xlsx)

    Why: who buys from whom between industries -> which company -> company edges exist in the graph.
    """
    raise NotImplementedError


def fetch_edgar(tickers: list[str], contact: str, force: bool = False):
    """D2. company_tickers.json + submissions/CIK##########.json -> data/raw/edgar/. Needs a User-Agent with contact.

    Why: SIC industry codes of the companies, the starting point of the company -> BEA industry mapping.
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("fetch.main is implemented by D1 and D2 (docs/plans/phases/F1_data_collection.md)")


if __name__ == "__main__":
    sys.exit(main())
