"""Metrics computed from run logs (owner: D1). Input: ``marketsim.sim.log_schema.load_run`` (contract K3).

Metrics read only the run log and never import simulation code, so they work for any run, with the
Python or the C++ engine.

Planned metrics: docs/model/model_description.md, section 8.

- ``series``: price and reference series of an asset with explicit empty-book handling.
- ``stability``: single-asset stability and liquidity metrics (phase F4), one value per asset.
- ``wave``: shock transmission metrics along a dependency chain (phase F5, experiment E4).
"""

from marketsim.metrics.series import as_of, price_series, reference_series
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
from marketsim.metrics.wave import (
    WAVE_METRICS,
    chain_amplification,
    shock_time,
    wave_metrics,
    wave_summary,
)

__all__ = [
    "WAVE_METRICS",
    "as_of",
    "chain_amplification",
    "empty_side_fraction",
    "execution_rate",
    "max_drawdown",
    "mean_depth5",
    "mean_spread",
    "price_series",
    "quote_impact",
    "reference_series",
    "shock_time",
    "stability_metrics",
    "valuation_error",
    "volatility",
    "wave_metrics",
    "wave_summary",
]
