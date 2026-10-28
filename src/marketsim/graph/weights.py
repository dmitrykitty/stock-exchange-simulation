"""Graph weights from stock prices (owner: D2).

For every company i, regress its daily log returns on the returns of its parents and of the market index:

    r_i,t = alpha_i + sum_j w_ij * r_j,t + beta_mkt_i * r_mkt,t + u_i,t

over the estimation period (2021-01-01 .. 2023-12-31). Two weight sets:
- "direct": parents are external nodes only (commodities + index)  -> used by myopic valuers;
- "full":   parents are external nodes and parent companies        -> used by network valuers.

Reference numbers to reproduce (752 trading days 2021-2023): docs/plans/phases/F2_dependency_graph.md.

Stability rule: keep the function signature and output columns; extend the implementation freely.
"""

from __future__ import annotations

import pandas as pd


def estimate_weights(
    returns: pd.DataFrame,
    edges: pd.DataFrame,
    market: str = "sp500",
    start: str = "2021-01-01",
    end: str = "2023-12-31",
) -> pd.DataFrame:
    """
    returns -- marketsim.data.api.load_returns_daily(): log returns of tickers, commodities and the index;
    edges   -- columns parent, child, source: the graph structure (no weights yet).

    Returns columns: parent, child, w_direct, w_full, t_stat_direct, t_stat_full, source.
    The market index edge (``market`` -> child) is added for every company.

    Why: the weights are the agents' knowledge of "how much this company moves when its parent moves".
    Used by: save_graph -> data/graph/<name>/ -> valuers in the simulation (S); reference_path (D2).
    """
    raise NotImplementedError("estimate_weights (owner: D2)")
