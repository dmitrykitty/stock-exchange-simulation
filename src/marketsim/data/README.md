# `marketsim.data`: data

**Owners:** D1 (price series), D2 (BEA, EDGAR, company mapping). What to do and how: `docs/plans/phases/F1_data_collection.md` (Russian).

## The main rule

Every other part of the project reads data **only** through the functions in `api.py`. Therefore **function names, parameters, file names, columns and dtypes are a contract**: they do not change without the procedure in `docs/model/contracts.md`. The implementation inside `fetch.py` and `clean.py` is yours: split it into helpers, add modules, add parameters with defaults, add your own tests under `tests/data/`.

## Files

| File | What | May change |
|---|---|---|
| `schemas.py` | every table: file, index, columns, dtypes; `validate()` | only via the contract procedure |
| `api.py` | reading (`load_*`) and writing (`write_table`) with schema validation | only via the contract procedure |
| `check.py` | `python -m marketsim.data.check`: validates every file | output may be improved |
| `fetch.py` | download into `data/raw/` | **you implement it** |
| `clean.py` | `data/raw/` → `data/processed/` | **you implement it** |

## Functions you implement: why they exist and who uses the result

| Function | Owner | What it does | Why the project needs it | Who uses the result |
|---|---|---|---|---|
| `fetch_fred(series_id)` | D1 | FRED CSV → `data/raw/fred/` | oil and gas are external signals for agents; the S&P 500 is a signal and a mandatory control in the regressions; monthly copper is a cross-check | `build_commodities`, `build_market` |
| `fetch_yahoo(tickers, adjusted)` | D1 | prices and volumes → `data/raw/yahoo/` | stock prices teach the agents (graph weights), give start prices and the validation benchmark; metal futures are external signals | `build_stocks`, `build_commodities` |
| `fetch_french()` | D1 | 49 industries + market factor → `data/raw/french/` | industry-level validation; backup market return | `build_ff49`, `build_market` |
| `fetch_worldbank()` | D1 | monthly prices → `data/raw/worldbank/` | official cross-check of Yahoo futures; backup for gold | `build_commodities` |
| `fetch_bea()` | D2 | input-output tables → `data/raw/bea/` | "who buys from whom" decides which company-to-company edges exist in the graph | `build_io` |
| `fetch_edgar(tickers, contact)` | D2 | SIC codes → `data/raw/edgar/` | starting point of the company-to-industry mapping | `companies.csv` checks |
| `build_calendar()` | D1 | `calendar.parquet` | one shared time axis; without it series silently shift around holidays | every daily table, the simulation (session ↔ date) |
| `build_commodities()` | D1 | `commodities_daily`, `commodities_monthly` | external signals need a value on every trading day | graph (D2), external scenarios (D2), validation (D1) |
| `build_market()` | D1 | `market_daily` | the index is an external signal and a regression control | graph (D2), scenarios (D2), validation (D1) |
| `build_stocks()` | D1 | `stocks_close_adj/raw`, `stocks_volume`, `returns_daily` | regression inputs, start prices, validation benchmark | graph (D2), validation (D1), simulation (start prices) |
| `build_ff49()` | D1 | `ff49_vw_daily` | industry-level validation | validation (D1) |
| `build_io(year)` | D2 | `io_{year}_A`, `io_{year}_industries` | graph structure | `graph.structure.candidate_company_edges` |

## Functions everybody uses (ready, do not change)

| Function | Returns | Called by |
|---|---|---|
| `load_calendar()` | trading days (`date` → `session`) | cleaning, graph, simulation, validation |
| `load_commodities_daily()` | daily commodity prices | graph, scenarios, validation |
| `load_market_daily()` | S&P 500 and the market factor | graph, scenarios, validation |
| `load_stock_prices(adjusted)` | stock prices | graph (weights, start prices), validation |
| `load_stock_volume()` | volumes | validation, agent calibration |
| `load_returns_daily()` | log returns of everything | graph (regressions), validation |
| `load_ff49()` | returns of 49 industries | validation |
| `load_companies()`, `tickers()` | the companies and their industries; row order = asset order on the exchange | everyone |
| `load_io_matrix(year)`, `load_io_industries(year)` | the BEA table | graph structure |
| `load_external(scenario)` | external signals per session | simulation, reference path, metrics |
| `write_table(df, spec)` | writes a table after validating it | `clean.py`, scenarios (D2) |
| `read_table(spec)` | reads and validates any table | loaders, `check.py` |
