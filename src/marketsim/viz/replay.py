"""Replay of a recorded run as one self-contained HTML page (F9).

The page shows the dependency graph with every company coloured by its price move, the price of a selected
company against the reference path R and the agents' mean valuation, its daily candles and the best quotes of
its book, the external signals and small charts of every company, with a time cursor that can be played,
paused and stepped.

It reads only the run log (contract K3), the dependency graph (K2) and, for scenarios on real dates, real close
prices through ``marketsim.data.api`` for comparison; it imports nothing from the simulation itself.

    python -m marketsim.viz.replay runs/<experiment>/<run_id> -o replay.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.data import api as data_api
from marketsim.data.schemas import validate
from marketsim.graph.api import DependencyGraph, load_graph, resolve_graph_dir
from marketsim.market.api import NO_PRICE
from marketsim.metrics.series import as_of, price_series, reference_series
from marketsim.sim.log_schema import RUN_TABLES, RunLog, validate_meta

TABLES = ("book", "trades", "valuations", "events")
# Samples per asset kept in the page; longer runs are thinned evenly (the last sample is always kept).
MAX_SAMPLES = 5000

EXTERNAL_LABELS = {"wti": "Crude oil (WTI)", "henry_hub": "Natural gas", "copper": "Copper", "gold": "Gold",
                   "sp500": "S&P 500"}
SECTORS = {"COP": "oil & gas", "EOG": "oil & gas", "VLO": "refining", "MPC": "refining", "NEE": "utility",
           "DUK": "utility", "DAL": "airline", "UAL": "airline", "ODFL": "trucking", "FCX": "copper mining",
           "NEM": "gold mining", "NUE": "steel", "AA": "aluminium", "NVDA": "chips", "AMD": "chips",
           "GOOGL": "internet", "META": "internet", "JPM": "bank"}
# Hand layout of real_v1 (columns = steps from the raw-material prices), as in docs/figures/f2_graph_real_v1.png.
REAL_V1_LAYOUT = {
    "wti": (0, 0.5), "henry_hub": (0, 3.1), "copper": (0, 4.8), "gold": (0, 6), "sp500": (0, 9),
    "COP": (1, 0), "EOG": (1, 1), "NEE": (1, 2.6), "DUK": (1, 3.6), "FCX": (1, 5), "NEM": (1, 6),
    "NVDA": (1, 7.4), "AMD": (1, 8.4), "JPM": (1, 9.8),
    "VLO": (2, 0), "MPC": (2, 1), "NUE": (2, 3.6), "AA": (2, 4.6), "GOOGL": (2, 7.4), "META": (2, 8.4),
    "DAL": (3, -0.5), "UAL": (3, 0.5), "ODFL": (3, 1.5),
}


def load_log(run_dir: str | Path, tables: tuple[str, ...] = TABLES) -> RunLog:
    """The run log with only ``tables`` read, each validated against its K3 spec (the page needs no signals)."""
    d = Path(run_dir)
    meta = validate_meta(json.loads((d / "meta.json").read_text(encoding="utf-8")))
    specs = {spec.name: spec for spec in RUN_TABLES}
    return RunLog(d, meta, {name: validate(pd.read_parquet(d / specs[name].file), specs[name]) for name in tables})


def _layout(graph: DependencyGraph) -> dict[str, tuple[float, float]]:
    """Node positions: the hand layout for real_v1, otherwise columns by the longest path from the externals."""
    if set(graph.nodes) <= set(REAL_V1_LAYOUT):
        return {n: REAL_V1_LAYOUT[n] for n in graph.nodes}
    depth = {n: 0 for n in graph.externals()}
    for _ in range(len(graph.nodes)):
        for e in graph.edges:
            if e.parent in depth and e.child != e.parent:
                depth[e.child] = max(depth.get(e.child, 0), depth[e.parent] + 1)
    for n in graph.companies():
        depth.setdefault(n, 1)
    columns: dict[int, list[str]] = {}
    for n in graph.nodes:
        columns.setdefault(depth[n], []).append(n)
    return {n: (col, row) for col, names in columns.items() for row, n in enumerate(names)}


def _graph_changed(graph_dir: Path, meta: dict) -> bool:
    recorded = meta.get("graph_sha256")
    if not recorded:
        return False
    current = hashlib.sha256(b"".join((graph_dir / f).read_bytes() for f in ("nodes.csv", "edges.csv")))
    return current.hexdigest() != recorded


def _external_frame(log: RunLog, scenario: str | None) -> pd.DataFrame:
    """External signals per session as the run saw them (``session_start`` events), with dates if the scenario
    has them."""
    events = log["events"]
    rows = [json.loads(p) for p in events.loc[events["kind"] == "session_start", "payload"]]
    frame = pd.DataFrame(rows).set_index("session").sort_index() if rows else pd.DataFrame()
    dates = None
    if scenario:
        try:
            dates = data_api.load_external(scenario)["date"]
        except FileNotFoundError:
            dates = None
    if dates is not None and len(frame):
        frame["date"] = dates.reindex(frame.index)
    return frame


def _real_closes(tickers: list[str], dates: pd.Series) -> dict[str, list[float | None]]:
    """Real close prices (adjusted for splits only, as the start prices) on the sessions' dates, in USD."""
    try:
        closes = data_api.load_stock_prices(adjusted=False)
    except FileNotFoundError:
        return {}
    closes.index = pd.to_datetime(closes.index)
    on_dates = closes.reindex(pd.to_datetime(dates.to_numpy()))
    return {t: _round_list(on_dates[t].to_numpy(dtype=float), 2) for t in tickers if t in on_dates.columns}


def _round_list(values: np.ndarray, digits: int) -> list[float | None]:
    return [None if not math.isfinite(v) else round(float(v), digits) for v in values]


def _sessions_ohlc(trades: pd.DataFrame, n_sessions: int) -> dict[str, list]:
    """Daily candles from every trade of a session (prices in ticks); a session without trades is a gap."""
    out = {k: [None] * n_sessions for k in ("o", "h", "l", "c")}
    out["v"] = [0] * n_sessions
    if not len(trades):
        return out
    t = trades.sort_values(["t", "seq"], kind="stable")
    session = np.floor(t["t"].to_numpy()).astype(int)
    grouped = t.assign(session=session).groupby("session")
    agg = grouped["price"].agg(["first", "max", "min", "last"]).join(grouped["qty"].sum())
    for s, row in agg.iterrows():
        if 0 <= s < n_sessions:
            out["o"][s], out["h"][s], out["l"][s], out["c"][s] = (int(row["first"]), int(row["max"]),
                                                                    int(row["min"]), int(row["last"]))
            out["v"][s] = int(row["qty"])
    return out


def build_payload(run_dir: str | Path, real_prices: bool = True, max_samples: int = MAX_SAMPLES) -> dict:
    """Everything the page draws, as plain JSON-ready data. Prices are in ticks (cents)."""
    log = load_log(run_dir)
    meta = log.meta
    config = meta["config"]
    assets: list[str] = list(meta["assets"])
    graph_name = config.get("graph", "single_asset")
    graph_dir = resolve_graph_dir(graph_name) if graph_name != "single_asset" else None
    graph = load_graph(graph_dir) if graph_dir else None

    book = log["book"]
    times_all = np.sort(book["t"].unique())
    step = max(1, math.ceil(len(times_all) / max_samples))
    keep = np.unique(np.r_[np.arange(0, len(times_all), step), len(times_all) - 1])
    times = times_all[keep]

    series = []
    for i, ticker in enumerate(assets):
        price = price_series(log, i).groupby(level=0).last()
        ref = reference_series(log, i).groupby(level=0).last()
        val = log["valuations"].loc[log["valuations"]["asset"] == i].groupby("t")["v_mean"].last()
        rows = book.loc[book["asset"] == i].groupby("t")[["best_bid", "best_ask", "depth5_bid", "depth5_ask"]].last()
        rows = rows.reindex(times)
        bid = rows["best_bid"].to_numpy(dtype=float)
        ask = rows["best_ask"].to_numpy(dtype=float)
        bid[bid == NO_PRICE] = np.nan
        ask[ask == NO_PRICE] = np.nan
        series.append({
            "ticker": ticker,
            "price": _round_list(as_of(price, times), 1),
            "ref": _round_list(as_of(ref, times), 1),
            "vmean": _round_list(as_of(val, times), 1),
            "bid": _round_list(bid, 0),
            "ask": _round_list(ask, 0),
            "depth_bid": [int(v) for v in rows["depth5_bid"].fillna(0)],
            "depth_ask": [int(v) for v in rows["depth5_ask"].fillna(0)],
            "candles": _sessions_ohlc(log["trades"].loc[log["trades"]["asset"] == i], int(config["n_sessions"])),
            "start": int(meta.get("start_prices_ticks", [0] * len(assets))[i]) or None,
        })

    names = data_api.load_companies().set_index("ticker")["name"].to_dict() if graph else {}
    for s in series:
        s["name"] = names.get(s["ticker"], s["ticker"])
        s["sector"] = SECTORS.get(s["ticker"], "")
        if s["start"] is None:
            s["start"] = next((v for v in s["price"] if v is not None), None)

    externals = _external_frame(log, config.get("external"))
    ext_names = [c for c in externals.columns if c != "date"]
    dates = externals["date"] if "date" in externals and externals["date"].notna().any() else None
    payload = {
        "run_id": meta["run_id"],
        "seed": meta["seed"],
        "graph": graph_name,
        "scenario": config.get("external"),
        "n_sessions": int(config["n_sessions"]),
        "warmup": int(config.get("warmup_sessions", 0)),
        "composition": config.get("agents", {}).get("composition", "custom"),
        "herding_h": config.get("agents", {}).get("herding", {}).get("h"),
        "investors": sum(1 for k in meta.get("population", {}).get("kinds", []) if k != "market_maker") or None,
        "t": [round(float(v), 4) for v in times],
        "assets": series,
        "externals": {
            "names": ext_names,
            "labels": [EXTERNAL_LABELS.get(n, n) for n in ext_names],
            "values": {n: _round_list(externals[n].to_numpy(dtype=float), 4) for n in ext_names},
        },
        "dates": [d.strftime("%Y-%m-%d") for d in pd.to_datetime(dates)] if dates is not None else None,
        "real": _real_closes(assets, dates) if (real_prices and dates is not None) else {},
        "graph_changed": bool(graph_dir and _graph_changed(graph_dir, meta)),
        "nodes": [], "edges": [],
    }
    if graph:
        positions = _layout(graph)
        for n, node in graph.nodes.items():
            x, y = positions[n]
            payload["nodes"].append({"id": n, "kind": node.kind, "x": x, "y": y,
                                     "label": EXTERNAL_LABELS.get(n, n)})
        for e in graph.edges:
            w = e.weight("full")
            payload["edges"].append({"parent": e.parent, "child": e.child, "source": e.source,
                                     "w": None if w is None else round(w, 3),
                                     "weak": e.t_stat_full is not None and abs(e.t_stat_full) < 2})
    return payload


def render(payload: dict, cdn: bool = False) -> str:
    """The page: the HTML template with the payload and plotly.js (inline, or from a CDN with ``cdn``)."""
    template = resources.files("marketsim.viz").joinpath("replay_template.html").read_text(encoding="utf-8")
    if cdn:
        plotly_tag = '<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.1.0/plotly.min.js"></script>'
    else:
        from plotly.offline import get_plotlyjs  # optional dependency: pip install -e ".[viz]"

        plotly_tag = f"<script>{get_plotlyjs()}</script>"
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return (template.replace("<!--PLOTLY-->", plotly_tag)
            .replace("/*PAYLOAD*/null", data)
            .replace("{{TITLE}}", f"Market replay {payload['run_id']}"))


def write_html(run_dir: str | Path, out: str | Path, real_prices: bool = True, cdn: bool = False) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(run_dir, real_prices=real_prices), cdn=cdn), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write a self-contained HTML replay of a recorded run.")
    parser.add_argument("run_dir", help="run directory with meta.json and the K3 tables")
    parser.add_argument("-o", "--out", default=None, help="output file (default: <run_dir>/replay.html)")
    parser.add_argument("--no-real", action="store_true", help="do not add real close prices for comparison")
    parser.add_argument("--cdn", action="store_true", help="load plotly.js from a CDN instead of inlining it")
    args = parser.parse_args(argv)
    out = args.out or Path(args.run_dir) / "replay.html"
    print(write_html(args.run_dir, out, real_prices=not args.no_real, cdn=args.cdn))


if __name__ == "__main__":
    main()
