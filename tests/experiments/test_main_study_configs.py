"""The configurations of the main study (docs/plans/main_study_protocol.md): grids, fresh seeds, shared cells."""

from dataclasses import asdict
from pathlib import Path

import pytest

from marketsim.experiments.config import expand, load_config
from marketsim.sim.settings import AgentSettings

MAIN = Path("configs/main")
SEEDS = set(range(5001, 5031))
EARLIER = [p for p in Path("configs").glob("*.yaml")]


def specs(name):
    return expand(load_config(MAIN / f"{name}.yaml"))


@pytest.mark.parametrize("name,runs,seeds", [
    ("e5", 270, SEEDS), ("e7_network0", 30, SEEDS), ("e7_network25", 30, SEEDS), ("e7_network50", 30, SEEDS),
    ("e6_no_stoploss", 60, SEEDS), ("e6_no_rebalancing", 60, SEEDS), ("e6_neither", 60, SEEDS),
    ("e6_shock", 120, SEEDS), ("controls/random_graph_1", 10, set(range(5001, 5011))),
    ("controls/random_graph_2", 10, set(range(5001, 5011))), ("controls/flat_inputs", 10, set(range(5001, 5011))),
])
def test_grids_and_seeds(name, runs, seeds):
    grid = specs(name)
    assert len(grid) == runs and {s.seed for s in grid} == seeds
    for spec in grid:
        AgentSettings.from_dict(spec.config.agents, graph_mode=True)


def test_main_seeds_were_never_used_before():
    used = {seed for path in EARLIER for seed in load_config(path).seeds}
    assert not used & SEEDS


def _agents(spec):
    a = AgentSettings.from_dict(spec.config.agents, graph_mode=True)
    return asdict(a)


def test_shared_cells_differ_only_by_the_switches():
    e5 = {(s.config.agents["composition"], s.config.agents["herding"]["h"]): s for s in specs("e5") if s.seed == 5001}
    mixed = _agents(e5[("mixed", 0.0)])
    switches = {"e6_no_stoploss": {"risk_limit"}, "e6_no_rebalancing": {"ablation"},
                "e6_neither": {"risk_limit", "ablation"}}
    for name, changed in switches.items():
        spec = next(s for s in specs(name) if s.seed == 5001 and s.config.agents["herding"]["h"] == 0.0)
        other = _agents(spec)
        assert {k for k in mixed if mixed[k] != other[k]} == changed, name
        assert asdict(spec.config) | {"name": "", "agents": {}} == asdict(e5[("mixed", 0.0)].config) | {
            "name": "", "agents": {}}
    # E7 at 30% would be the mixed E5 cell at h = 0.2: the same market with 150 network valuers.
    e7 = _agents(specs("e7_network25")[0])
    base = _agents(e5[("mixed", 0.2)])
    assert {k for k in base if base[k] != e7[k]} <= {"valuers_myopic", "valuers_network", "composition"}
