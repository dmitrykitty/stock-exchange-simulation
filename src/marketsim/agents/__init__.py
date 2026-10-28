"""Agents (owner: S): valuers (myopic and network), trend followers, noise traders, market makers,
rebalancing funds, risk-limit traders, herding over the acquaintance network.

The valuation update rule is defined in docs/model/model_description.md, section 4.2.
Agents talk to the market only through ``marketsim.market.MarketGateway`` (contract K1).
"""
