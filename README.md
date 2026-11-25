# marketsim: agent-based stock market with an asset dependency graph

Course project for "Symulacja systemów dyskretnych" (Discrete Systems Simulation), AGH.

About 525 investors and 36 market makers trade the shares of 18 real US companies on simulated limit order books. **Nobody sets the stock prices**: they emerge from trades. During a run, the only real-world inputs are external signals: commodity prices (oil, gas, copper, gold) and the S&P 500 index. Agents know a **dependency graph** ("Delta moves 0.22% when Valero moves 1%") and update their valuations along it, so an oil shock travels from stock to stock **through trades**. We study how agent strategies, herding and capital concentration amplify, dampen or distort that wave.

| Start here | |
|---|---|
| **Team checklist (Russian): start here** | [docs/team_checklist.md](docs/team_checklist.md) |
| Plain-language explainer (Russian) | [docs/model/explainer.md](docs/model/explainer.md) |
| Model description (Russian) | [docs/model/model_description.md](docs/model/model_description.md) |
| Research proposal (Polish) | [docs/research_proposal.md](docs/research_proposal.md) |
| Progress overview with results (Polish) | [docs/progress_overview.md](docs/progress_overview.md) |
| Main study protocol (Russian) | [docs/plans/main_study_protocol.md](docs/plans/main_study_protocol.md) |
| Plan, phases F0–F10, roles (Russian) | [docs/plans/overall_plan.md](docs/plans/overall_plan.md) |
| Interfaces between parts | [docs/model/contracts.md](docs/model/contracts.md) |
| **Repository rules** (team and AI agents) | [AGENTS.md](AGENTS.md) |

## Team roles

| Role | Phases | Plan |
|---|---|---|
| **O**: exchange engine (C++) and gateway | F3 → F6 → F9 | [docs/plans/roles/engine_owner.md](docs/plans/roles/engine_owner.md) |
| **D1**: price series, model validation | F1 → F8 → F9 | [docs/plans/roles/data_team.md](docs/plans/roles/data_team.md) |
| **D2**: company mapping, dependency graph | F1 → F2 → F5–F6 | [docs/plans/roles/data_team.md](docs/plans/roles/data_team.md) |
| **S**: simulation and agents | F4 → F5 → F6 → F7 | [docs/plans/roles/simulation_owner.md](docs/plans/roles/simulation_owner.md) |

## Quick start

Python 3.11+.

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev,data]"
```

```bash
pytest
```

All parts are implemented, so the tests should pass; a test is skipped only when the raw data it needs has not been downloaded (`data/raw/`, not in git).

Check which data files exist and whether they follow their contract:

```bash
python -m marketsim.data.check
```

Build the C++ engine (needs a C++20 compiler and CMake; not needed for data work):

```bash
pip install -e cpp/
```

## Layout

```text
docs/                 documentation (Russian; the proposal, progress overview and reports are in Polish)
  model/              model, plain-language explainer, contracts between parts
  plans/              overall plan, per-role plans, detailed phases F0-F10
src/marketsim/        Python package
  market/             K1: exchange and gateway (api.py = contract; py_exchange.py = reference; cpp_exchange.py = C++ adapter)
  data/               data schemas and loaders (contract); fetch.py, clean.py (D1/D2); check.py
  graph/              K2: dependency graph (api.py = format; structure.py, weights.py, reference.py = D2)
  sim/                simulation (S); log_schema.py = K3 run-log format
  experiments/        config.py = K4 experiment configuration format
  agents/ metrics/ viz/
  analysis/           main-study tables and report figures
  validation/         F8 checks against the real data 2024-2025
cpp/                  C++ engine lob_core: include/ src/ bindings/ tests/
tests/contracts/      contract tests: the executable specification
configs/              experiment configurations (YAML, K4)
data/
  raw/ processed/     downloaded and cleaned data (not in git, reproducible by scripts)
  mapping/            companies.csv: the 18 companies and their BEA industries (in git)
  graph/              dependency graphs (in git)
  fixtures/           small test data (in git)
notebooks/            analysis
```

## Contracts

A contract is the interface through which one part of the project uses another. **Change a contract only following [docs/model/contracts.md](docs/model/contracts.md)**: a PR approved by the owner and by every consumer. Inside an implementation, anything goes.

| Contract | Files | Owner | Consumers |
|---|---|---|---|
| **K1** exchange and gateway | `src/marketsim/market/api.py`, `cpp/include/lob_core/*.hpp` | O | S |
| **Data** schemas and loaders | `src/marketsim/data/schemas.py`, `src/marketsim/data/api.py` | D1, D2 | everyone |
| **K2** graph and external signals | `src/marketsim/graph/api.py`, `EXTERNAL` in `data/schemas.py` | D2 | S, D1 |
| **K3** run log | `src/marketsim/sim/log_schema.py` | S | D1, O |
| **K4** experiment config | `src/marketsim/experiments/config.py`, `configs/*.yaml` | S | everyone |

## Companies

18 companies, fixed on 2026-10-28 (`data/mapping/companies.csv`). Every one has complete daily prices for 2021–2025 (1255 NYSE trading days, no gaps) and a verified SIC code from SEC EDGAR.

| BEA industry | Companies | Main external parent (daily beta 2021–2023) |
|---|---|---|
| 211 Oil and gas extraction | COP, EOG | WTI +0.52 / +0.55 |
| 324 Petroleum refining | VLO, MPC | WTI +0.43 / +0.35 |
| 22 Utilities | NEE, DUK | natural gas ≈ 0 (weak) |
| 481 Air transportation | DAL, UAL | WTI −0.04 / −0.07 (via VLO: +0.22) |
| 484 Truck transportation | ODFL | WTI ≈ 0 (weak) |
| 212 Mining, except oil and gas | FCX, NEM | copper +1.02 / gold +1.29 |
| 331 Primary metals | NUE, AA | copper +0.37 / +0.96 |
| 334 Computer and electronic products | NVDA, AMD | market only |
| 514 Data processing, internet | GOOGL, META | NVDA +0.09 (manual AI edge) |
| 521CI Banks | JPM | market only (control company) |

## Status

Phases F1–F9 are implemented: data, the dependency graph `real_v1`, the exchange (Python and C++), the
single-asset market (F4), graph valuers on a chain (F5), the full 18-company network on the real signals
2024–2025 (F6), experiments E5–E7 (F7), validation against the real data (F8) and the replay page and live
mode (F9). The main study (690 runs on fresh seeds under a frozen protocol) is computed; the report is phase F10.

Run one experiment (a new output directory for each; an interrupted run continues where it stopped):

```bash
python -m marketsim.experiments.run configs/main/e5.yaml --out runs/main/e5
```

The whole main study with its tables, validation checks and figures (about 6 h on 10 processes):

```bash
scripts/run_main_study.sh
```

Guides: [F4 single asset](docs/guides/f4_simulation.md), [F5 graph valuers](docs/guides/f5_graph_valuers.md),
[F6 full network](docs/guides/f6_full_network.md), [F7 experiments](docs/guides/f7_experiments.md),
[F9 visualization](docs/guides/f9_visualization.md).
