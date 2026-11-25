"""K4 type checks: bool is a subclass of int in Python and must not pass as a number."""

from dataclasses import asdict, replace

import pytest
import yaml

from marketsim.experiments.config import ConfigError, RunConfig, load_config

BASE = RunConfig(name="t", exchange_backend="python", graph="graph_chain", external="oil_shock",
                 use_market_index=True, n_sessions=5, warmup_sessions=1, sample_dt=0.05)


@pytest.mark.parametrize("change", [{"n_sessions": True}, {"warmup_sessions": False}, {"n_sessions": 5.0},
                                    {"sample_dt": True}, {"sample_dt": "0.05"}, {"use_market_index": 1}])
def test_bools_and_wrong_types_are_rejected(change):
    with pytest.raises(ConfigError):
        replace(BASE, **change).validate()


def test_integer_sample_dt_is_still_a_number():
    assert replace(BASE, sample_dt=1).validate().sample_dt == 1


@pytest.mark.parametrize("seeds", [[True, False], [1, True], [1.0]])
def test_seeds_must_be_integers_not_bools(tmp_path, seeds):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"base": asdict(BASE), "seeds": seeds}))
    with pytest.raises(ConfigError, match="seeds"):
        load_config(path)
