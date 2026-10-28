"""Contracts K3 (run log) and K4 (experiment configuration)."""

from __future__ import annotations

import json

import pandas as pd
import pytest
import yaml

from marketsim import paths
from marketsim.data.schemas import ContractError
from marketsim.experiments.config import ConfigError, expand, load_config
from marketsim.sim import log_schema as L

PILOT = paths.PROJECT_ROOT / "configs" / "pilot.yaml"


def test_pilot_config_loads_and_expands():
    exp = load_config(PILOT)
    runs = expand(exp)
    assert len(runs) == 3 * 3  # three values of h x three seeds
    assert sorted({r.config.agents["herding"]["h"] for r in runs}) == [0.0, 0.3, 0.6]
    assert exp.base.agents["herding"]["h"] == 0.3  # the base config is not mutated


def _write(tmp_path, raw):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_wrong_grid_key_is_an_error(tmp_path):
    raw = yaml.safe_load(PILOT.read_text())
    raw["grid"] = {"agents.herding.hh": [0.1]}
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, raw))


def test_unknown_field_is_an_error(tmp_path):
    raw = yaml.safe_load(PILOT.read_text())
    raw["base"]["n_session"] = 5
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, raw))


def _empty(spec):
    dtypes = {"f": "float64", "i": "int64", "s": "string"}
    return pd.DataFrame({c.name: pd.Series(dtype=dtypes[c.kind]) for c in spec.columns})


def test_run_log_roundtrip(tmp_path):
    for spec in L.RUN_TABLES:
        _empty(spec).to_parquet(tmp_path / spec.file)
    meta = {k: None for k in L.META_KEYS} | {"log_version": L.LOG_VERSION}
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    run = L.load_run(tmp_path)
    assert set(run.tables) == {s.name for s in L.RUN_TABLES}


def test_run_log_requires_meta_keys(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"run_id": "x"}))
    with pytest.raises(ContractError):
        L.load_run(tmp_path)
