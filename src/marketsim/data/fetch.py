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

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from marketsim import paths

START, END = "2021-01-01", "2026-01-01"  # END is exclusive in yfinance

FRED_DAILY = ("DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "SP500")
FRED_MONTHLY = ("PCOPPUSDM",)
YAHOO_FUTURES = ("CL=F", "NG=F", "HG=F", "GC=F", "PL=F")

WORLDBANK_PAGE_URL = "https://www.worldbank.org/en/research/commodity-markets"
WORLDBANK_FALLBACK_URL = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026"
    "/related/CMO-Historical-Data-Monthly.xlsx"
)
BEA_ZIP_URL = "https://apps.bea.gov/industry/iTables%20Static%20Files/AllTablesIO.zip"
BEA_XLSX_NAME = "IOUse_Before_Redefinitions_PRO_1997-2023_Summary.xlsx"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_manifest(path: Path, url: str) -> None:
    """Record metadata about a downloaded file in data/raw/manifest.json."""
    raw = paths.raw_dir()
    raw.mkdir(parents=True, exist_ok=True)
    manifest_path = raw / "manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    rel_key = path.relative_to(raw).as_posix()
    entry = {
        "url": url,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }

    if isinstance(manifest.get("files"), dict):
        manifest["files"][rel_key] = entry
    else:
        manifest[rel_key] = entry

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _download_file(url: str, dest: Path, force: bool = False, headers: dict[str, str] | None = None) -> Path:
    """Download a file as-is and register it in manifest.json."""
    if dest.exists() and not force:
        print(f"[skip] {dest} already exists")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    req_headers = {"User-Agent": "Mozilla/5.0 (compatible; marketsim/1.0)"}
    if headers:
        req_headers.update(headers)

    print(f"[fetch] {url} -> {dest}")
    resp = requests.get(url, headers=req_headers, timeout=120)
    resp.raise_for_status()
    data = resp.content

    dest.write_bytes(data)
    _record_manifest(dest, url)
    return dest


def _load_company_tickers() -> list[str]:
    """Read the 18 company tickers from data/mapping/companies.csv."""
    comp_csv = paths.mapping_dir() / "companies.csv"
    if not comp_csv.exists():
        return [
            "COP", "EOG", "VLO", "MPC", "NEE", "DUK", "DAL", "UAL", "ODFL",
            "FCX", "NEM", "NUE", "AA", "NVDA", "AMD", "GOOGL", "META", "JPM",
        ]
    with open(comp_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["ticker"].strip() for row in reader if row.get("ticker")]


def fetch_fred(series_id: str, force: bool = False) -> Path:
    """D1. CSV without a key: https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID> -> data/raw/fred/<ID>.csv

    Why: oil, gas (external signals), S&P 500 (signal + regression control), monthly copper (cross-check).
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    dest = paths.raw_dir() / "fred" / f"{series_id}.csv"
    return _download_file(url, dest, force=force)


def fetch_yahoo(tickers: list[str], adjusted: bool, force: bool = False) -> tuple[Path, Path]:
    """D1. yfinance==1.7.0, START..END -> data/raw/yahoo/close_{adj|raw}.csv and volume.csv

    Why: stock prices (graph weights, start prices, validation) and metal futures (external signals).
    """
    import yfinance as yf

    yahoo_dir = paths.raw_dir() / "yahoo"
    yahoo_dir.mkdir(parents=True, exist_ok=True)

    suffix = "adj" if adjusted else "raw"
    close_path = yahoo_dir / f"close_{suffix}.csv"
    vol_path = yahoo_dir / "volume.csv"

    need_close = force or not close_path.exists()
    need_vol = force or not vol_path.exists()

    if not need_close and not need_vol:
        print(f"[skip] {close_path} already exists")
        return close_path, vol_path

    print(f"[fetch] yfinance ({len(tickers)} tickers, adjusted={adjusted}) -> {close_path}")
    df = yf.download(
        tickers=tickers,
        start=START,
        end=END,
        auto_adjust=adjusted,
        progress=False,
        threads=False,
    )

    close_df = df["Close"]
    close_df.to_csv(close_path)
    _record_manifest(
        close_path,
        f"yfinance://{','.join(tickers)}?start={START}&end={END}&adjusted={adjusted}&field=Close",
    )

    if need_vol and "Volume" in df:
        vol_df = df["Volume"]
        vol_df.to_csv(vol_path)
        _record_manifest(vol_path, f"yfinance://{','.join(tickers)}?start={START}&end={END}&field=Volume")

    return close_path, vol_path


def fetch_french(force: bool = False) -> list[Path]:
    """D1. 49_Industry_Portfolios_daily_CSV.zip and F-F_Research_Data_Factors_daily_CSV.zip -> data/raw/french/

    Why: industry-level validation and a backup market return.
    """
    base_url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
    archives = (
        "49_Industry_Portfolios_daily_CSV.zip",
        "F-F_Research_Data_Factors_daily_CSV.zip",
    )
    out = []
    for name in archives:
        url = f"{base_url}/{name}"
        dest = paths.raw_dir() / "french" / name
        out.append(_download_file(url, dest, force=force))
    return out


def fetch_worldbank(force: bool = False) -> Path:
    """D1. CMO-Historical-Data-Monthly.xlsx (take the link from the web page) -> data/raw/worldbank/

    Why: official monthly prices to cross-check Yahoo futures; backup source for gold and platinum.
    """
    dest = paths.raw_dir() / "worldbank" / "CMO-Historical-Data-Monthly.xlsx"
    if dest.exists() and not force:
        print(f"[skip] {dest} already exists")
        return dest

    xlsx_url = WORLDBANK_FALLBACK_URL
    try:
        resp = requests.get(
            WORLDBANK_PAGE_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; marketsim/1.0)"},
            timeout=30,
        )
        resp.raise_for_status()
        m = re.search(r'https?://[^\s"\'<>]+CMO-Historical-Data-Monthly\.xlsx', resp.text)
        if m:
            xlsx_url = m.group(0)
    except requests.RequestException as exc:
        print(f"[warn] World Bank page scrape failed ({exc}); using fallback URL")

    return _download_file(xlsx_url, dest, force=force)


def fetch_bea(force: bool = False) -> Path:
    """D2. AllTablesIO.zip -> data/raw/bea/ (needs IOUse_Before_Redefinitions_PRO_1997-2023_Summary.xlsx)

    Why: who buys from whom between industries -> which company -> company edges exist in the graph.
    """
    bea_dir = paths.raw_dir() / "bea"
    zip_dest = bea_dir / "AllTablesIO.zip"
    _download_file(BEA_ZIP_URL, zip_dest, force=force)

    xlsx_dest = bea_dir / BEA_XLSX_NAME
    if not xlsx_dest.exists() or force:
        with zipfile.ZipFile(zip_dest, "r") as zf:
            members = [name for name in zf.namelist() if name.endswith(BEA_XLSX_NAME)]
            if members:
                data = zf.read(members[0])
                xlsx_dest.write_bytes(data)
                _record_manifest(xlsx_dest, f"{BEA_ZIP_URL}!/{members[0]}")
    return zip_dest


def fetch_edgar(tickers: list[str], contact: str, force: bool = False) -> list[Path]:
    """D2. company_tickers.json + submissions/CIK##########.json -> data/raw/edgar/. Needs a User-Agent with contact.

    Why: SIC industry codes of the companies, the starting point of the company -> BEA industry mapping.
    """
    if not contact or not contact.strip():
        raise ValueError("SEC EDGAR requires a contact in User-Agent (set SEC_USER_AGENT env var)")

    headers = {"User-Agent": contact.strip()}
    edgar_dir = paths.raw_dir() / "edgar"
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    tickers_dest = edgar_dir / "company_tickers.json"
    _download_file(tickers_url, tickers_dest, force=force, headers=headers)

    raw_tickers = json.loads(tickers_dest.read_text(encoding="utf-8"))
    cik_map = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in raw_tickers.values() if "ticker" in v}

    saved: list[Path] = [tickers_dest]
    sub_dir = edgar_dir / "submissions"
    sub_dir.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        t_upper = ticker.strip().upper()
        if t_upper not in cik_map:
            raise KeyError(f"Ticker {t_upper!r} not found in SEC company_tickers.json")
        cik = cik_map[t_upper]
        fname = f"CIK{cik:010d}.json"
        sub_url = f"https://data.sec.gov/submissions/{fname}"
        sub_dest = sub_dir / fname
        need_sleep = force or not sub_dest.exists()
        _download_file(sub_url, sub_dest, force=force, headers=headers)
        saved.append(sub_dest)
        if need_sleep:
            time.sleep(0.15)

    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="comma-separated subset of sources: fred,yahoo,french,worldbank,bea,edgar",
    )
    parser.add_argument(
        "--contact",
        type=str,
        default="",
        help="contact string for SEC EDGAR User-Agent (defaults to SEC_USER_AGENT env var)",
    )
    args = parser.parse_args(argv)

    all_sources = ("fred", "yahoo", "french", "worldbank", "bea", "edgar")
    if args.only:
        selected = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in selected if s not in all_sources]
        if unknown:
            print(f"Unknown sources: {unknown}. Valid: {all_sources}", file=sys.stderr)
            return 2
    else:
        selected = list(all_sources)

    company_tickers = _load_company_tickers()

    if "fred" in selected:
        for sid in (*FRED_DAILY, *FRED_MONTHLY):
            fetch_fred(sid, force=args.force)

    if "yahoo" in selected:
        all_yahoo = [*company_tickers, *YAHOO_FUTURES]
        fetch_yahoo(all_yahoo, adjusted=True, force=args.force)
        fetch_yahoo(company_tickers, adjusted=False, force=args.force)

    if "french" in selected:
        fetch_french(force=args.force)

    if "worldbank" in selected:
        fetch_worldbank(force=args.force)

    if "bea" in selected:
        fetch_bea(force=args.force)

    if "edgar" in selected:
        contact = (
            args.contact
            or os.environ.get("SEC_USER_AGENT", "")
            or "AGH University stock-exchange-simulation project student@agh.edu.pl"
        )
        fetch_edgar(company_tickers, contact=contact, force=args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
