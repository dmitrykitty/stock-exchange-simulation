"""Metrics computed from run logs (owner: D1). Input: ``marketsim.sim.log_schema.load_run`` (contract K3).

Metrics read only the run log and never import simulation code, so they work for any run, with the
Python or the C++ engine.

Planned metrics: docs/model/model_description.md, section 8.

- ``stability``: single-asset stability and liquidity metrics (phase F4), one value per asset.
"""

from marketsim.metrics.stability import (
    empty_side_fraction,
    execution_rate,
    max_drawdown,
    mean_depth5,
    mean_spread,
    quote_impact,
    stability_metrics,
    valuation_error,
    volatility,
)

__all__ = [
    "empty_side_fraction", "execution_rate", "max_drawdown", "mean_depth5", "mean_spread",
    "quote_impact", "stability_metrics", "valuation_error", "volatility",
]
