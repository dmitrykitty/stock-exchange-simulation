"""Graph structure from economics (owner: D2): which company -> company edges make sense.

Rule: a supplier -> customer edge exists if the customer's industry spends at least ``threshold`` of its
output on the supplier industry's products (BEA input-output table). Details and code snippets:
docs/plans/phases/F2_dependency_graph.md.

Stability rule: keep the function signature and output columns; extend the implementation freely.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from marketsim.data import api as data_api
from marketsim.graph.api import DependencyGraph, Edge, Node
from marketsim.graph.weights import estimate_weights

EXTERNAL_NODES: tuple[str, ...] = ("wti", "henry_hub", "copper", "gold", "sp500")

# Explicit commodity -> company edges (source = "commodity") with economic justifications.
COMMODITY_EDGES: tuple[tuple[str, str, str], ...] = (
    (
        "wti",
        "COP",
        "Crude oil exploration and production revenue directly tracks WTI price",
    ),
    (
        "wti",
        "EOG",
        "Crude oil exploration and production revenue directly tracks WTI price",
    ),
    (
        "wti",
        "VLO",
        "Crude oil is the primary feedstock of petroleum refiners; refining margins co-move with WTI",
    ),
    (
        "wti",
        "MPC",
        "Crude oil is the primary feedstock of petroleum refiners; refining margins co-move with WTI",
    ),
    (
        "wti",
        "DAL",
        "Jet fuel cost exposure for scheduled airline; direct weight is weak without refiner pass-through",
    ),
    (
        "wti",
        "UAL",
        "Jet fuel cost exposure for scheduled airline; direct weight is borderline without refiner pass-through",
    ),
    (
        "wti",
        "ODFL",
        "Diesel fuel input cost for LTL trucking carrier; mitigated by fuel surcharges",
    ),
    (
        "henry_hub",
        "COP",
        "Natural gas co-production revenue of upstream oil and gas producer",
    ),
    (
        "henry_hub",
        "EOG",
        "Natural gas co-production revenue of upstream oil and gas producer",
    ),
    (
        "henry_hub",
        "NEE",
        "Natural gas fuel input for gas-fired electricity generation (regulated fuel pass-through)",
    ),
    (
        "henry_hub",
        "DUK",
        "Natural gas fuel input for gas-fired electricity generation (regulated fuel pass-through)",
    ),
    (
        "copper",
        "FCX",
        "Copper mining revenue directly follows COMEX copper price",
    ),
    (
        "copper",
        "AA",
        "Global industrial base-metals demand cycle shared by primary aluminum and copper",
    ),
    (
        "copper",
        "NUE",
        "Industrial construction and manufacturing metals cycle shared by steel and copper",
    ),
    (
        "gold",
        "NEM",
        "Gold mining revenue directly follows spot and futures gold price",
    ),
)

# Manual company -> company edges outside BEA current-cost threshold (CapEx AI hardware -> AI services).
MANUAL_COMPANY_EDGES: tuple[tuple[str, str, str], ...] = (
    (
        "NVDA",
        "GOOGL",
        "AI accelerator GPUs supplied to cloud and AI data-center services (capital expenditure not in BEA current use)",
    ),
    (
        "AMD",
        "GOOGL",
        "Data-center CPUs and AI accelerators supplied to cloud and internet services (CapEx link)",
    ),
    (
        "NVDA",
        "META",
        "AI accelerator GPUs supplied for AI training and inference infrastructure (CapEx link)",
    ),
    (
        "AMD",
        "META",
        "Data-center CPUs and AI accelerators supplied for cloud and AI infrastructure (CapEx link)",
    ),
)

# Review of all 18 BEA industry-to-industry pairs (54 company-to-company candidate edges at threshold >= 2%):
# maps (parent_bea, child_bea) -> (decision, included_in_real_v1, justification_template).
BEA_INDUSTRY_DECISIONS: dict[tuple[str, str], tuple[str, bool, str]] = {
    # --- Core supply-chain edges: KEEP (10 edges) ---
    (
        "211",
        "324",
    ): (
        "keep",
        True,
        "BEA 2023: petroleum refining (324) spends {share:.1%} of output on oil and gas extraction (211)",
    ),
    (
        "324",
        "481",
    ): (
        "keep",
        True,
        "BEA 2023: air transportation (481) spends {share:.1%} of output on refined petroleum jet fuel (324)",
    ),
    (
        "324",
        "484",
    ): (
        "keep",
        True,
        "BEA 2023: truck transportation (484) spends {share:.1%} of output on refined petroleum diesel fuel (324)",
    ),
    # --- Borderline ~2% utilities -> primary metals edges: QUESTIONABLE -> KEEP per model spec (4 edges) ---
    (
        "22",
        "331",
    ): (
        "questionable",
        True,
        "BEA 2023: primary metals (331) spend {share:.1%} of output on utilities (22); electricity-intensive steel EAF and aluminum smelting",
    ),
    # --- Intra-industry bilateral pairs (parent_bea == child_bea): DROP (14 edges) ---
    (
        "211",
        "211",
    ): (
        "drop",
        False,
        "Intra-industry pair (211 <-> 211, {share:.1%}): COP and EOG are upstream E&P competitors, not supplier and customer; creates 2-cycle and collinearity",
    ),
    (
        "324",
        "324",
    ): (
        "drop",
        False,
        "Intra-industry pair (324 <-> 324, {share:.1%}): VLO and MPC are competing downstream refiners; creates 2-cycle and masks upstream oil suppliers",
    ),
    (
        "22",
        "22",
    ): (
        "drop",
        False,
        "Intra-industry pair (22 <-> 22, {share:.1%}): NEE and DUK are regional electric utilities in the same tier; creates 2-cycle",
    ),
    (
        "212",
        "212",
    ): (
        "drop",
        False,
        "Intra-industry pair (212 <-> 212, {share:.1%}): FCX mines copper and NEM mines gold; unrelated metals aggregated into BEA 212",
    ),
    (
        "331",
        "331",
    ): (
        "drop",
        False,
        "Intra-industry pair (331 <-> 331, {share:.1%}): NUE produces steel and AA produces primary aluminum; separate metal chains aggregated into BEA 331",
    ),
    (
        "334",
        "334",
    ): (
        "drop",
        False,
        "Intra-industry pair (334 <-> 334, {share:.1%}): NVDA and AMD are competing fabless semiconductor designers, not supplier and customer",
    ),
    (
        "514",
        "514",
    ): (
        "drop",
        False,
        "Intra-industry pair (514 <-> 514, {share:.1%}): GOOGL and META are competing internet/AI platforms, not supplier and customer",
    ),
    # --- Other inter-industry BEA pairs near threshold or mismatched sub-industries: QUESTIONABLE / DROP (26 edges) ---
    (
        "211",
        "22",
    ): (
        "questionable",
        False,
        "Borderline BEA link (211 -> 22, {share:.1%}): utilities burn natural gas, captured directly via henry_hub -> NEE/DUK; COP/EOG equity tracks crude oil (t <= 1.1)",
    ),
    (
        "324",
        "22",
    ): (
        "drop",
        False,
        "Borderline BEA link (324 -> 22, {share:.1%}): petroleum liquids represent <1% of US utility generation for NEE/DUK",
    ),
    (
        "22",
        "211",
    ): (
        "drop",
        False,
        "Borderline BEA link (22 -> 211, {share:.1%}): field power is a minor operating cost for global E&P producers and would create a cycle with 211",
    ),
    (
        "22",
        "212",
    ): (
        "drop",
        False,
        "Borderline BEA link (22 -> 212, {share:.1%}): global copper/gold mines of FCX and NEM do not depend on regional US utility rates of NEE/DUK",
    ),
    (
        "212",
        "331",
    ): (
        "questionable",
        False,
        "Sub-industry mismatch (212 -> 331, {share:.1%}): BEA 331 includes copper/gold smelting, but NUE (steel) and AA (aluminum) do not smelt copper/gold ore from FCX/NEM",
    ),
    (
        "331",
        "211",
    ): (
        "drop",
        False,
        "Borderline BEA link (331 -> 211, {share:.1%}): drill pipe/casing is oilfield CapEx; steel/aluminum equities do not drive daily upstream E&P valuation",
    ),
    (
        "484",
        "331",
    ): (
        "drop",
        False,
        "Non-core logistics link (484 -> 331, {share:.1%}): LTL carrier ODFL is not a primary input price driver for steel or aluminum smelting",
    ),
}


def candidate_company_edges(io_matrix: pd.DataFrame, companies: pd.DataFrame, threshold: float = 0.02) -> pd.DataFrame:
    """
    io_matrix -- marketsim.data.api.load_io_matrix(year): A.loc[j, i] = share of industry i's output spent on j;
    companies -- marketsim.data.api.load_companies().

    Returns columns: parent, child (tickers), parent_bea, child_bea, cost_share.
    Pairs within the same company are excluded; pairs within the same industry are kept only if
    the industry buys from itself above the threshold.

    Why: limits company -> company edges to economically meaningful supplier -> customer links, so that the
    graph does not connect everything that merely correlates.
    Used by: estimate_weights (D2), then the graph files read by the simulation (S).
    """
    edges = []

    for _, child in companies.iterrows():
        child_ticker = child["ticker"]
        child_bea = str(child["bea_code"])

        if child_bea not in io_matrix.columns:
            raise ValueError(f"Company {child_ticker} has bea_code {child_bea} which is missing from io_matrix columns.")

        for _, parent in companies.iterrows():
            parent_ticker = parent["ticker"]
            parent_bea = str(parent["bea_code"])

            if child_ticker == parent_ticker:
                continue

            if parent_bea not in io_matrix.index:
                raise ValueError(f"Company {parent_ticker} has bea_code {parent_bea} which is missing from io_matrix index.")

            cost_share = float(io_matrix.loc[parent_bea, child_bea])

            if cost_share >= threshold:
                edges.append({
                    "parent": parent_ticker,
                    "child": child_ticker,
                    "parent_bea": parent_bea,
                    "child_bea": child_bea,
                    "cost_share": cost_share,
                })

    if not edges:
        return pd.DataFrame(columns=["parent", "child", "parent_bea", "child_bea", "cost_share"])

    return pd.DataFrame(edges)


def commodity_edges() -> pd.DataFrame:
    """Explicit commodity -> company edges with source='commodity' and economic justification."""
    return pd.DataFrame(
        [
            {"parent": parent, "child": child, "source": "commodity", "justification": justification}
            for parent, child, justification in COMMODITY_EDGES
        ],
        columns=["parent", "child", "source", "justification"],
    )


def index_edges(companies: pd.DataFrame, market: str = "sp500") -> pd.DataFrame:
    """Explicit market index -> company edges (one for every traded company) with source='index'."""
    return pd.DataFrame(
        [
            {
                "parent": market,
                "child": str(ticker),
                "source": "index",
                "justification": "Common US equity market factor (S&P 500)",
            }
            for ticker in companies["ticker"]
        ],
        columns=["parent", "child", "source", "justification"],
    )


def manual_company_edges() -> pd.DataFrame:
    """Explicit manual company -> company edges (AI semiconductors -> internet/AI services)."""
    return pd.DataFrame(
        [
            {"parent": parent, "child": child, "source": "manual", "justification": justification}
            for parent, child, justification in MANUAL_COMPANY_EDGES
        ],
        columns=["parent", "child", "source", "justification"],
    )


def review_bea_candidate_edges(
    io_matrix: pd.DataFrame,
    companies: pd.DataFrame,
    threshold: float = 0.02,
) -> pd.DataFrame:
    """Classify all BEA candidate edges into 'keep', 'drop', or 'questionable' with written justification."""
    cand = candidate_company_edges(io_matrix, companies, threshold=threshold).copy()
    decisions: list[str] = []
    included_flags: list[bool] = []
    justifications: list[str] = []

    for row in cand.itertuples(index=False):
        key = (str(row.parent_bea), str(row.child_bea))
        if key not in BEA_INDUSTRY_DECISIONS:
            raise ValueError(f"Unclassified BEA industry pair {key} for edge {row.parent} -> {row.child}")
        decision, included, template = BEA_INDUSTRY_DECISIONS[key]
        decisions.append(decision)
        included_flags.append(included)
        justifications.append(template.format(share=float(row.cost_share)))

    cand["decision"] = decisions
    cand["included"] = included_flags
    cand["justification"] = justifications
    return cand


def _format_justification_with_weak_note(
    base_justification: str,
    t_direct: float | None,
    t_full: float | None,
) -> str:
    """Append an explicit note when estimated regression weight(s) have |t| < 2."""
    weak_parts: list[str] = []
    if t_direct is not None and not np.isnan(t_direct) and abs(t_direct) < 2.0:
        weak_parts.append(f"direct t={t_direct:.2f}")
    if t_full is not None and not np.isnan(t_full) and abs(t_full) < 2.0:
        weak_parts.append(f"full t={t_full:.2f}")
    if not weak_parts:
        return base_justification
    return f"{base_justification} [weak weight kept: {', '.join(weak_parts)}, |t| < 2]"


def build_real_v1_graph(
    start: str = "2021-01-01",
    end: str = "2023-12-31",
    start_price_date: str = "2024-01-02",
    io_year: int = 2023,
    threshold: float = 0.02,
    computed_on: str | None = None,
) -> DependencyGraph:
    """Assemble and validate the full 18-company dependency graph ``real_v1``."""
    companies = data_api.load_companies()
    io_matrix = data_api.load_io_matrix(io_year)
    returns = data_api.load_returns_daily()
    raw_prices = data_api.load_stock_prices(adjusted=False)

    ts_start_price = pd.Timestamp(start_price_date)
    if ts_start_price not in raw_prices.index:
        raise ValueError(f"start_price_date {start_price_date!r} not found in stocks_close_raw index")
    start_prices = raw_prices.loc[ts_start_price]

    bea_review = review_bea_candidate_edges(io_matrix, companies, threshold=threshold)
    bea_kept = bea_review[bea_review["included"]].copy()
    bea_edges_df = pd.DataFrame(
        {
            "parent": bea_kept["parent"].to_numpy(),
            "child": bea_kept["child"].to_numpy(),
            "source": "bea",
            "justification": bea_kept["justification"].to_numpy(),
        }
    )

    comm_df = commodity_edges()
    idx_df = index_edges(companies, market="sp500")
    man_df = manual_company_edges()

    all_structure_edges = pd.concat([comm_df, idx_df, bea_edges_df, man_df], ignore_index=True)

    # Order edges by company order in companies.csv for a canonical edges.csv layout
    company_order = companies["ticker"].tolist()
    ordered_chunks = [all_structure_edges[all_structure_edges["child"] == ticker] for ticker in company_order]
    ordered_structure_edges = pd.concat(ordered_chunks, ignore_index=True)

    weights_df = estimate_weights(
        returns=returns,
        edges=ordered_structure_edges[["parent", "child", "source"]],
        market="sp500",
        start=start,
        end=end,
    )

    just_map = {
        (str(r.parent), str(r.child)): str(r.justification)
        for r in ordered_structure_edges.itertuples(index=False)
    }

    nodes: dict[str, Node] = {}
    for ext_name in EXTERNAL_NODES:
        nodes[ext_name] = Node(name=ext_name, kind="external")
    for row in companies.itertuples(index=False):
        ticker = str(row.ticker)
        nodes[ticker] = Node(
            name=ticker,
            kind="company",
            ticker=ticker,
            bea_code=str(row.bea_code),
            start_price=round(float(start_prices[ticker]), 4),
        )

    edges_list: list[Edge] = []
    for row in weights_df.itertuples(index=False):
        w_dir = None if pd.isna(row.w_direct) else round(float(row.w_direct), 6)
        w_full = None if pd.isna(row.w_full) else round(float(row.w_full), 6)
        t_dir = None if pd.isna(row.t_stat_direct) else round(float(row.t_stat_direct), 4)
        t_full = None if pd.isna(row.t_stat_full) else round(float(row.t_stat_full), 4)
        base_just = just_map[(str(row.parent), str(row.child))]
        justification = _format_justification_with_weak_note(base_just, t_dir, t_full)
        edges_list.append(
            Edge(
                parent=str(row.parent),
                child=str(row.child),
                w_direct=w_dir,
                w_full=w_full,
                t_stat_direct=t_dir,
                t_stat_full=t_full,
                source=str(row.source),
                justification=justification,
            )
        )

    est_slice = returns.loc[(returns.index >= start) & (returns.index <= end)].dropna()
    est_start = est_slice.index.min().strftime("%Y-%m-%d")
    est_end = est_slice.index.max().strftime("%Y-%m-%d")
    decision_counts = bea_review["decision"].value_counts().to_dict()

    meta = {
        "name": "real_v1",
        "description": (
            "Dependency graph of 18 US companies, 4 commodities (wti, henry_hub, copper, gold), "
            "and the S&P 500 market index. Structure from BEA 2023 input-output tables (threshold 2%), "
            "commodity exposure, and manual CapEx AI hardware links; weights from daily log-return OLS (2021-2023)."
        ),
        "estimation_period": f"{est_start}..{est_end}",
        "n_days": len(est_slice),
        "io_year": io_year,
        "bea_threshold": threshold,
        "bea_candidates_total": len(bea_review),
        "bea_candidates_summary": {
            "keep": int(decision_counts.get("keep", 0)),
            "questionable": int(decision_counts.get("questionable", 0)),
            "drop": int(decision_counts.get("drop", 0)),
            "included_in_graph": int(bea_review["included"].sum()),
        },
        "weak_weights_policy": (
            "Economically justified edges with |t| < 2 are retained in the graph so valuer agents hold the "
            "full structural belief set; their justification explicitly records the insignificant t-statistic. "
            "Economically spurious or intra-industry competitor edges are dropped during structure selection."
        ),
        "start_price_date": start_price_date,
        "start_price_source": "stocks_close_raw (Yahoo Finance close adjusted for splits only, not dividends)",
        "computed_on": computed_on or date.today().isoformat(),
    }

    return DependencyGraph(nodes=nodes, edges=edges_list, meta=meta).validate()
