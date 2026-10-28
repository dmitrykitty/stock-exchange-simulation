# Data

| Folder | What | In git |
|---|---|---|
| `raw/` | files exactly as downloaded + `manifest.json` (URL, date, size, SHA-256) | no |
| `processed/` | cleaned tables following `src/marketsim/data/schemas.py` | no |
| `mapping/` | manual work: `companies.csv` (companies → BEA industries) | yes |
| `graph/` | dependency graphs (contract K2) | yes |
| `fixtures/` | small test data: the `graph_chain` graph and the `external_oil_shock.csv` scenario | yes |

How to produce `raw/` and `processed/`: `docs/plans/phases/F1_data_collection.md`. Validation: `python -m marketsim.data.check`.

## Data decisions

Everything that affects results goes here: what was filled, what was dropped, which source was chosen and why.

| Date | Decision |
|---|---|
| 2026-10-28 | Company list fixed: 18 companies in `mapping/companies.csv`, all `confirmed`. Each has complete daily prices 2021–2025 (1255 NYSE days, no gaps) and a SIC code from EDGAR; BEA industries assigned by SIC and business profile. |
| 2026-10-28 | ConocoPhillips is mapped to BEA 211 (oil and gas extraction) although its EDGAR SIC is 2911 (refining): the SIC predates the 2012 Phillips 66 spin-off. |
| 2026-10-28 | Platinum is collected but has no company in the graph. Natural gas has near-zero daily betas for all companies (t < 1); its edges stay with near-zero weights. |
| 2026-10-28 | No copper → semiconductor edge: daily betas of NVDA and AMD on copper are insignificant (t = −0.5, −1.1). |
