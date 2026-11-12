"""F9 replay page: built from a short network run on the chain fixture, reads only logs and contracts."""

import ast
import json
from pathlib import Path

import pytest

from marketsim.experiments.config import RunConfig
from marketsim.sim.api import run
from marketsim.viz import replay


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    config = RunConfig(
        name="viz", exchange_backend="python", graph="graph_chain", external="oil_shock", use_market_index=True,
        n_sessions=8, warmup_sessions=2, sample_dt=0.05,
        agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                "total_cash_cents": 6_000_000, "total_shares": 2_400},
    )
    return run(config, seed=3, out_dir=tmp_path_factory.mktemp("runs"))


def test_payload_has_one_series_per_asset_and_session(run_dir):
    payload = replay.build_payload(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    n = len(payload["t"])
    assert [a["ticker"] for a in payload["assets"]] == meta["assets"]
    assert payload["t"] == sorted(payload["t"]) and n > 8
    for a in payload["assets"]:
        for key in ("price", "ref", "vmean", "bid", "ask", "depth_bid", "depth_ask"):
            assert len(a[key]) == n, key
        assert all(len(a["candles"][k]) == payload["n_sessions"] for k in ("o", "h", "l", "c", "v"))
        assert a["start"] == pytest.approx(a["price"][0], rel=0.05)
    # The shocked chain has nodes and edges; every edge connects known nodes.
    ids = {node["id"] for node in payload["nodes"]}
    assert {e["parent"] for e in payload["edges"]} | {e["child"] for e in payload["edges"]} <= ids
    # External signals come from the run's own session_start events: oil steps up after warm-up.
    oil = payload["externals"]["values"]["wti"]
    assert len(oil) == payload["n_sessions"] and oil[-1] > oil[0]
    assert payload["dates"] is None and payload["real"] == {}


def test_candles_match_trades(run_dir):
    payload = replay.build_payload(run_dir)
    trades = replay.load_log(run_dir)["trades"]
    first = trades.loc[(trades["asset"] == 0) & (trades["t"] < 1)].sort_values(["t", "seq"])
    c = payload["assets"][0]["candles"]
    assert (c["o"][0], c["h"][0], c["l"][0], c["c"][0], c["v"][0]) == (
        first["price"].iloc[0], first["price"].max(), first["price"].min(), first["price"].iloc[-1], first["qty"].sum())


def test_long_runs_are_thinned_and_keep_the_last_sample(run_dir):
    payload = replay.build_payload(run_dir, max_samples=20)
    full = replay.build_payload(run_dir)
    assert len(payload["t"]) <= 21 and payload["t"][-1] == full["t"][-1]


def test_page_embeds_the_payload_safely(run_dir, tmp_path):
    html = replay.render(replay.build_payload(run_dir), cdn=True)
    assert "/*PAYLOAD*/null" not in html and "{{TITLE}}" not in html
    start = html.index("const P = ") + len("const P = ")
    data = json.loads(html[start:html.index(";\n", start)])
    assert data["run_id"] == json.loads((run_dir / "meta.json").read_text())["run_id"]
    assert "</script" not in html[start:html.index(";\n", start)]


def test_reads_only_logs_and_contracts():
    """The page code may use the K3 log schema, the K1 constants, data/graph loaders and metrics helpers only."""
    source = Path(replay.__file__).read_text(encoding="utf-8")
    allowed = ("marketsim.sim.log_schema", "marketsim.market.api", "marketsim.data", "marketsim.graph.api",
               "marketsim.metrics")
    for module in _imported_modules(source):
        if module.startswith("marketsim"):
            assert module == "marketsim.viz" or module.startswith(allowed), module


def _imported_modules(source):
    """Every module named by ``import a.b`` and ``from a.b import c``; a relative import is reported as such."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield f"marketsim.<relative level {node.level}>" if node.level else node.module


def test_import_boundary_check_sees_every_import_form():
    found = list(_imported_modules("import marketsim.sim.api\nfrom . import live\nfrom ..sim import network\n"
                                   "from marketsim.metrics import series\n"))
    assert found == ["marketsim.sim.api", "marketsim.<relative level 1>", "marketsim.<relative level 2>",
                     "marketsim.metrics"]


def test_page_shows_only_the_traded_companies(run_dir, tmp_path):
    # A run on a subset of the graph (F6 ``agents.assets``): meta.assets lists the traded companies and the asset
    # indices of the tables follow it. Made here from the full chain run by keeping its first two companies.
    import shutil

    import pandas as pd

    subset = tmp_path / "subset"
    shutil.copytree(run_dir, subset)
    meta = json.loads((subset / "meta.json").read_text())
    meta["assets"] = meta["assets"][:2]
    (subset / "meta.json").write_text(json.dumps(meta))
    for name in ("book", "trades", "valuations", "orders", "signals"):
        path = subset / f"{name}.parquet"
        table = pd.read_parquet(path)
        table.loc[table["asset"] < 2].reset_index(drop=True).to_parquet(path)
    payload = replay.build_payload(subset)
    companies = {n["id"] for n in payload["nodes"] if n["kind"] == "company"}
    assert companies == {a["ticker"] for a in payload["assets"]} == set(meta["assets"])
    assert len(companies) == 2
    assert {n["id"] for n in payload["nodes"] if n["kind"] == "external"}, "external nodes stay"
    ids = {n["id"] for n in payload["nodes"]}
    assert payload["edges"] and all(e["parent"] in ids and e["child"] in ids for e in payload["edges"])


def test_markup_in_the_run_id_stays_text(run_dir):
    payload = replay.build_payload(run_dir)
    payload["run_id"] = "</title><script>alert(1)</script>{{TITLE}}<!--PLOTLY-->"
    page = replay.render(payload, cdn=True)
    assert "<script>alert(1)" not in page
    assert page.count("<!--PLOTLY-->") == 0 and page.count("plotly.min.js") == 1
    title = page[page.index("<title>") + 7:page.index("</title>")]
    assert title.startswith("Market replay &lt;/title&gt;&lt;script&gt;")
    start = page.index("const P = ") + len("const P = ")
    assert json.loads(page[start:page.index(";\n", start)])["run_id"] == payload["run_id"]


@pytest.mark.parametrize("real_prices", [True, False])
def test_sessions_without_dates_stay_empty(run_dir, monkeypatch, real_prices):
    import pandas as pd

    dates = pd.Series(pd.to_datetime(["2024-01-02", "2024-01-03"] + [None] * 6), name="date")
    monkeypatch.setattr(replay.data_api, "load_external", lambda scenario: pd.DataFrame({"date": dates}))
    payload = replay.build_payload(run_dir, real_prices=real_prices)
    assert payload["dates"][:2] == ["2024-01-02", "2024-01-03"] and payload["dates"][2:] == [None] * 6
    for series in payload["real"].values():
        assert all(v is None for v in series[2:])
