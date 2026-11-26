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
