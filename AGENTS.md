# Repository rules

Mandatory for everyone who works in this repository: team members and AI coding agents.

## Language

- Code, identifiers, comments, docstrings, error messages, commit messages, branch names, file and directory names: **English**.
- README files: English. Documentation under `docs/`: Russian. `docs/research_proposal.md` and the final report: Polish.

## Git

- Never push to `main`. Work on a branch named `<area>/<short-description>` (areas: `engine`, `data`, `graph`, `sim`, `agents`, `metrics`, `viz`, `docs`, `ci`, `fix`) and open a pull request.
- Small commits, imperative messages prefixed with the area: `engine: add self-trade check`.
- Run `pytest` before proposing a PR.

## Ownership and review

A pull request is approved by the owner of the area it touches; contract changes need the owner and every consumer (`docs/model/contracts.md`). Merge with squash after approval and green CI.

| Path | Owner |
|---|---|
| `cpp/`, `src/marketsim/market/` | O |
| `src/marketsim/data/`, `data/mapping/` | D1 (price series), D2 (BEA, EDGAR, mapping) |
| `src/marketsim/graph/`, `data/graph/` | D2 |
| `src/marketsim/sim/`, `src/marketsim/agents/`, `src/marketsim/experiments/`, `configs/` | S |
| `src/marketsim/metrics/`, `src/marketsim/validation/` | D1 |
| `src/marketsim/viz/` | D1 (charts), O (order book, live mode) |
| `tests/contracts/` | owner of the corresponding contract |
| `docs/`, `README.md`, `AGENTS.md` | everyone |

## Contracts (do not break)

The project is split into parts connected by frozen interfaces. Do not change public signatures, file names, table columns, dtypes or enum values of:

- K1 exchange and gateway: `src/marketsim/market/api.py`, `cpp/include/lob_core/types.hpp`, `cpp/include/lob_core/exchange.hpp`
- data schemas and loaders: `src/marketsim/data/schemas.py`, `src/marketsim/data/api.py`
- K2 dependency graph and external signals: `src/marketsim/graph/api.py`, signatures in `src/marketsim/graph/{structure,weights,reference}.py`
- K3 run log: `src/marketsim/sim/log_schema.py`
- K4 experiment config: `src/marketsim/experiments/config.py`

Implementing the stubs (functions that raise `NotImplementedError`) and adding private helpers or new modules is fine. Never edit `tests/contracts/` to make an implementation pass; a contract change needs the procedure in `docs/model/contracts.md`.

## Code rules

- Python 3.11+, type hints on public functions, `ruff check .` clean.
- Randomness only through `numpy.random.Generator` seeded from the run seed; same config + seed must give the same run log.
- Prices are integer ticks, cash is integer cents. No floating point in prices.
- Read data only through `marketsim.data.api`; write processed tables only through `marketsim.data.api.write_table`.
- Never commit `data/raw/`, `data/processed/`, `runs/`, secrets or personal e-mail addresses.

## Useful commands

```bash
pip install -e ".[dev,data]"      # Python package (no C++ needed)
pip install -e cpp/               # C++ engine lob_core (needs a C++20 compiler and CMake)
pytest                            # contract tests: "skipped" = not implemented yet, "failed" = broken
python -m marketsim.data.check    # validate all data files against their schemas
```

## Where things are

- Model and rationale: `docs/model/model_description.md`; plain-language explainer: `docs/model/explainer.md`
- Contracts explained: `docs/model/contracts.md`
- Plans: `docs/plans/overall_plan.md`; per-phase instructions: `docs/plans/phases/F0..F10`
