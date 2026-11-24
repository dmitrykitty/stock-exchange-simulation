"""Report figures are drawn from the study tables alone."""

import pandas as pd
import pytest

pytest.importorskip("matplotlib")

from marketsim.analysis import figures


def test_p6_figures_from_tables(tmp_path):
    e6 = tmp_path / "e6"
    e6.mkdir()
    rows = [{"channels": c, "h": h, "metric": m, "mean": 0.1, "ci_low": 0.05, "ci_high": 0.15}
            for c in ("all", "no_stoploss", "no_rebalancing", "neither") for h in (0.0, 0.4)
            for m in ("resid_unrelated", "resid_unrelated_stress", "risk_triggers")]
    pd.DataFrame(rows).to_csv(e6 / "p6_means.csv", index=False)
    rows = [{"change": c, "h": h, "metric": f"{g}_{k}", "mean": -0.01, "ci_low": -0.02, "ci_high": 0.0}
            for c in (-0.2, -0.5) for h in (0.0, 0.6) for g in ("target", "linked", "unrelated", "unrelated_abs")
            for k in (1, 5, 20)]
    pd.DataFrame(rows).to_csv(e6 / "p6_shock_effects.csv", index=False)
    paths = figures.p6(tmp_path, tmp_path / "out")
    assert [p.name for p in paths] == ["f7_p6_channels.png", "f7_p6_shock.png"]
    assert all(p.stat().st_size > 10_000 for p in paths)
