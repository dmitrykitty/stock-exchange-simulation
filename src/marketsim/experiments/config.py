"""Contract K4: experiment configuration (YAML).

One YAML file = one experiment: a base run configuration, a list of seeds and a parameter grid.
The top-level fields below are fixed by the contract. The ``agents`` section belongs to S and is free-form
(S validates it inside the simulation), so agent parameters can evolve without changing this contract.

Grid keys are dotted paths into the base config, for example ``agents.herding.h``. A grid key must point to
an existing field; typos are errors.
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

BACKENDS = ("python", "cpp")


class ConfigError(ValueError):
    """The configuration does not follow contract K4."""


@dataclass(frozen=True)
class RunConfig:
    name: str
    exchange_backend: str          # "python" | "cpp"
    graph: str                     # graph directory or name (contract K2)
    external: str                  # external-signal scenario name (contract K2)
    use_market_index: bool         # whether the S&P 500 node is visible to agents
    n_sessions: int                # trading sessions (1 session = 1 trading day)
    warmup_sessions: int           # sessions excluded from metrics
    sample_dt: float               # sampling step of book/valuation snapshots, in session units
    agents: dict = field(default_factory=dict)  # owned by S, free-form

    def validate(self) -> RunConfig:
        if self.exchange_backend not in BACKENDS:
            raise ConfigError(f"exchange_backend must be one of {BACKENDS}")
        if self.n_sessions <= 0 or not 0 <= self.warmup_sessions < self.n_sessions:
            raise ConfigError("need n_sessions > 0 and 0 <= warmup_sessions < n_sessions")
        if self.sample_dt <= 0:
            raise ConfigError("sample_dt must be positive")
        return self


@dataclass(frozen=True)
class ExperimentConfig:
    base: RunConfig
    seeds: tuple[int, ...]
    grid: dict[str, list]


@dataclass(frozen=True)
class RunSpec:
    config: RunConfig
    seed: int
    overrides: dict


def _from_dict(d: dict) -> RunConfig:
    expected = {f for f in RunConfig.__dataclass_fields__}
    unknown = set(d) - expected
    missing = {f for f in expected if f != "agents"} - set(d)
    if unknown or missing:
        raise ConfigError(f"run config: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    return RunConfig(**d).validate()


def load_config(path: str | Path) -> ExperimentConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    unknown = set(raw) - {"base", "seeds", "grid"}
    if unknown:
        raise ConfigError(f"unknown top-level keys {sorted(unknown)}")
    seeds = raw.get("seeds")
    if not seeds or not all(isinstance(s, int) for s in seeds):
        raise ConfigError("seeds must be a non-empty list of integers")
    grid = raw.get("grid") or {}
    base = _from_dict(raw["base"])
    for key in grid:
        _set_dotted(asdict(base), key, None)  # raises on a wrong path
    return ExperimentConfig(base, tuple(seeds), grid)


def _set_dotted(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    node = d
    for k in keys[:-1]:
        if not isinstance(node, dict) or k not in node:
            raise ConfigError(f"grid key {dotted!r}: no field {k!r}")
        node = node[k]
    if not isinstance(node, dict) or keys[-1] not in node:
        raise ConfigError(f"grid key {dotted!r}: no field {keys[-1]!r}")
    node[keys[-1]] = value


def expand(exp: ExperimentConfig) -> list[RunSpec]:
    """Cartesian product of the grid x seeds. Order: grid combinations, then seeds."""
    keys = list(exp.grid)
    out = []
    for values in itertools.product(*(exp.grid[k] for k in keys)) if keys else [()]:
        d = copy.deepcopy(asdict(exp.base))
        overrides = dict(zip(keys, values))
        for k, v in overrides.items():
            _set_dotted(d, k, v)
        cfg = _from_dict(d)
        out.extend(RunSpec(cfg, seed, overrides) for seed in exp.seeds)
    return out
