# Interim report

`interim_report.tex` (Polish) with the bibliography `references.bib` is the source; `interim_report.pdf` is the
compiled report, committed so it can be read on GitHub. The figures come from `docs/figures/`
(`python -m marketsim.analysis.figures --root runs/main --out docs/figures`).

Every entry of `references.bib` was checked against Crossref and carries a DOI or a URL. The data sources give
the download dates recorded in `data/raw/manifest.json`.

## Build

The report needs XeLaTeX (fontspec, Latin Modern) and BibTeX. With [Tectonic](https://tectonic-typesetting.github.io),
which downloads the TeX packages it needs on the first run:

```bash
TECTONIC=/path/to/tectonic docs/report/build.sh
```

With a TeX Live installation instead:

```bash
cd docs/report && latexmk -xelatex interim_report.tex
```

## Reproduce the empirical results

Run the commands from the repository root with the Python environment described in the top-level README.
The study protocol was frozen at 99c62a7. The four commit IDs recorded by the runs have the same simulation
modules and main configurations. See section 11 of docs/plans/main_study_protocol.md for the historical
versions and post-study corrections. Use the study version to regenerate historical runs and this report
revision for the analyses. The raw run logs are not distributed in Git.

| Result | Source under runs/main | Command from the repository root |
|---|---|---|
| E5 strategy contrasts, E6 channel and news responses, E7 network shares | e5/p5_*, e6/p6_*, e7/p7_* | python -m marketsim.analysis.study --root runs/main |
| Exposure benchmarks, commodity events, weights, lags, correlations and bands | validation/*.csv | python -m marketsim.validation.run checks --root runs/main |
| Experiment and validation figures | The study and validation CSVs above | python -m marketsim.analysis.figures --root runs/main --out docs/figures |
| Supplementary WTI regression for VLO and MPC | e7/network*/ run logs and the five external series | python docs/report/refinery_response.py |

The E6b figure combines p6_shock_extra.csv (the close of the news session) and p6_shock_effects.csv
(subsequent closes). Graph selection decisions are in data/graph/real_v1/bea_candidates_review.csv.
Company selection and economic roles are documented in data/mapping/companies.csv.

The accompanying result_tables.zip contains the study's aggregate tables (contrasts, validation results
and the company-level figure data), with paths relative to runs/main. The E5 and E7 cell-mean tables
average p5_metrics.csv and p7_metrics.csv over noncollapsed runs, grouped by composition and herding for E5
and by the share of network valuers among all investors for E7, including the shared E5 cell at 30%.
The archive omits individual run histories,
identifiers of market participants and raw market data. The direct-exposure validation and first-close
analyses were added in commits 06c153c and 2d27a9f; the supplementary oil regression is bundled with this revision.
